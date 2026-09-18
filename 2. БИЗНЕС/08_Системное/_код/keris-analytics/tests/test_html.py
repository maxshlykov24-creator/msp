from pathlib import Path

ROOT = Path(__file__).resolve().parents[4] / "04_Производство" / "Активные" / "Keris_Club" / "03_Проекты" / "Дашборд"
HTML = (ROOT / "dashboard.html").read_text(encoding="utf-8")


def test_booking_tokens_and_no_overload():
    assert "--bg:#fafaf2" in HTML
    assert "--rose:#e0a8bb" in HTML
    assert 'var(--font):-apple-system' in HTML or "--font:-apple-system" in HTML
    assert "fonts.googleapis.com" not in HTML
    assert "Merriweather" not in HTML
    assert "LTV" not in HTML
    assert "data-period=\"today\"" in HTML
    assert "data-period=\"week\"" in HTML
    assert "data-period=\"month\"" in HTML
    assert "data-dir=\"pet\"" in HTML
    assert "details-toggle" in HTML
    assert "min(100%,520px)" in HTML or "min(100%, 520px)" in HTML
    assert "combinedHero" in HTML
    assert "hoursPair" in HTML
    assert "show_avg_check" in HTML
    assert "60+" in HTML
