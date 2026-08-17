from pathlib import Path
from xml.etree import ElementTree

TEMPLATE = Path(__file__).parents[1] / "unraid" / "revolving-plex-manager.xml"


def test_unraid_template_runs_as_appdata_owner() -> None:
    root = ElementTree.parse(TEMPLATE).getroot()

    extra_params = root.findtext("ExtraParams", default="")
    assert "--user=99:100" in extra_params.split()

    config_mount = next(
        config for config in root.findall("Config") if config.get("Target") == "/config"
    )
    assert config_mount.get("Mode") == "rw"
