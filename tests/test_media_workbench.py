from app.persistence.models import IntegrationInstance, MediaIdentity, MediaLifecycle
from app.services.media_workbench import WorkbenchRow, build_workbench_page


def test_workbench_paginates_title_groups() -> None:
    integration = IntegrationInstance(
        id="radarr",
        kind="RADARR",
        name="Radarr",
        base_url="http://radarr:7878",
        credentials_encrypted="encrypted",
    )
    rows = [
        WorkbenchRow(
            lifecycle=MediaLifecycle(
                id=f"lifecycle-{number}",
                identity_id=f"identity-{number}",
                integration_id=integration.id,
                arr_item_id=number,
                state="ACTIVE",
            ),
            identity=MediaIdentity(
                id=f"identity-{number}",
                media_type="MOVIE",
                source_key=f"tmdb:{number}",
                canonical_title=f"Movie {number:02d}",
            ),
            integration=integration,
            torrent_count=0,
        )
        for number in range(52)
    ]

    page = build_workbench_page(rows, sort="title", page=2)

    assert page.page == 2
    assert page.page_count == 2
    assert page.total_entries == 52
    assert [entry.title for entry in page.entries] == ["Movie 50", "Movie 51"]


def test_workbench_summarizes_partial_and_full_series_protection() -> None:
    integration = IntegrationInstance(
        id="sonarr",
        kind="SONARR",
        name="Sonarr",
        base_url="http://sonarr:8989",
        credentials_encrypted="encrypted",
    )
    rows = [
        WorkbenchRow(
            lifecycle=MediaLifecycle(
                id=f"lifecycle-{season}",
                identity_id=f"identity-{season}",
                integration_id=integration.id,
                arr_item_id=7,
                state="ACTIVE",
                protection_state="PROTECTED" if season == 1 else "UNPROTECTED",
            ),
            identity=MediaIdentity(
                id=f"identity-{season}",
                media_type="SEASON",
                source_key=f"tvdb:100:season:{season}",
                canonical_title=f"The Show · Season {season}",
                season_number=season,
            ),
            integration=integration,
            torrent_count=0,
        )
        for season in (1, 2)
    ]

    entry = build_workbench_page(rows, sort="title", page=1).entries[0]

    assert entry.protected_count == 1

    rows[1].lifecycle.protection_state = "PROTECTED"
    assert entry.protected_count == 2
