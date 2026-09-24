from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable

GH = "https://raw.githubusercontent.com"


@dataclass
class UrlFilesCollector:
    name: str
    urls: dict[str, str]

    def collect(self, out_dir: Path, http) -> list[Path]:
        return [http.download(url, out_dir / filename) for filename, url in self.urls.items()]


CQI = UrlFilesCollector("cqi", {
    "arabica_2018.csv": f"{GH}/jldbc/coffee-quality-database/master/data/arabica_data_cleaned.csv",
    "robusta_2018.csv": f"{GH}/jldbc/coffee-quality-database/master/data/robusta_data_cleaned.csv",
    "arabica_2023.csv": f"{GH}/fatih-boyar/coffee-quality-data-CQI/main/df_arabica_clean.csv",
})
ROASTERDB = UrlFilesCollector("roasterdb", {
    "roasterdb_sample.csv": f"{GH}/RoasterDB/specialty-coffee-roasterdb/main/samples/roasterdb_sample.csv",
})
SCA = UrlFilesCollector("sca_wheel", {
    "sca_coffee_flavors.json": f"{GH}/fschlz/coffee-flavor-api/master/resources/sca_coffee_flavors.json",
})


def _kaggle_download(dataset: str, dest: Path) -> None:
    from kaggle.api.kaggle_api_extended import KaggleApi

    api = KaggleApi()
    api.authenticate()  # reads ~/.kaggle/kaggle.json
    api.dataset_download_files(dataset, path=str(dest), unzip=True, quiet=True)


@dataclass
class KaggleCollector:
    name: str = "coffeereview_kaggle"
    datasets: tuple[str, ...] = (
        "patkle/coffeereviewcom-over-7000-ratings-and-reviews",
        "hanifalirsyad/coffee-scrap-coffeereview",
        "schmoyote/coffee-reviews-dataset",
    )
    downloader: Callable[[str, Path], None] | None = field(default=None, repr=False)

    def collect(self, out_dir: Path, http) -> list[Path]:
        dl = self.downloader or _kaggle_download
        files: list[Path] = []
        for ds in self.datasets:
            d = out_dir / ds.replace("/", "__")
            d.mkdir(parents=True, exist_ok=True)
            dl(ds, d)
            files += sorted(d.glob("*.csv"))
        return files
