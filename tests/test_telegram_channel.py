from telegram_channel import is_allowed_private_sender


def test_whitelist_allows_configured_private_user() -> None:
    """配置中的用户从私聊发消息时可以通过。"""
    assert is_allowed_private_sender(
        123,
        "private",
        123,
    )


def test_whitelist_rejects_other_user_and_group() -> None:
    """其他用户和群聊都不能进入 Agent。"""
    assert not is_allowed_private_sender(
        456,
        "private",
        123,
    )
    assert not is_allowed_private_sender(
        123,
        "group",
        123,
    )