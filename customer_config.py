"""
Customer Configuration
======================
Maps Slack channels to customers and defines customer-specific settings.

Add customers here with their Slack channel IDs and docs folder.
"""

# Customer definitions
# IMPORTANT: Use Slack Channel IDs, not names.
# Find ID: Right-click channel > View channel details > scroll to Channel ID
CUSTOMERS = {
    "takeda": {
        "name": "Takeda",
        "channels": ["C038ET6BRNH"],
        "channel_names": ["takeda"],  # For matching Slack message metadata
        "docs_folder": "content_input/customer_docs/takeda",
    },
    "novartis": {
        "name": "Novartis",
        "channels": ["C07BKGVMSTZ"],
        "channel_names": ["novartis"],
        "docs_folder": "content_input/customer_docs/novartis",
    },
    "almirall": {
        "name": "Almirall",
        "channels": ["C02G3TMJU7R"],
        "channel_names": ["almirall"],
        "docs_folder": "content_input/customer_docs/almirall",
    },
    # Add more customers as needed
}


def get_customer_by_channel(channel_id: str) -> str | None:
    """Get customer key from Slack channel ID.

    Args:
        channel_id: Slack channel ID (e.g., "C0123ABC")

    Returns:
        Customer key (e.g., "novartis") or None if not a customer channel
    """
    for customer_key, config in CUSTOMERS.items():
        if channel_id in config.get("channels", []):
            return customer_key
    return None


def get_customer_by_channel_name(channel_name: str) -> str | None:
    """Get customer key from Slack channel name.

    Used to match Slack message metadata which stores channel names.

    Args:
        channel_name: Slack channel name (e.g., "takeda")

    Returns:
        Customer key or None if not a customer channel
    """
    for customer_key, config in CUSTOMERS.items():
        if channel_name in config.get("channel_names", []):
            return customer_key
    return None


def get_customer_config(customer_key: str) -> dict | None:
    """Get customer configuration by key.

    Args:
        customer_key: Customer key (e.g., "novartis")

    Returns:
        Customer config dict or None if not found
    """
    return CUSTOMERS.get(customer_key)


def get_all_customer_keys() -> list:
    """Get list of all customer keys."""
    return list(CUSTOMERS.keys())
