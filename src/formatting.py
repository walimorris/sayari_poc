"""Display-formatting helpers shared by the Streamlit app and the offline
narrative-generation script, so both present risk-factor ids the same way."""


def humanize_factor_id(raw_id: str) -> str:
    """Turns a Sayari-internal id like 'sanctioned_usa_ofac_sdn' into
    'Sanctioned usa ofac sdn' for display to non-technical readers."""
    return raw_id.replace("_", " ").capitalize()
