"""The HTML builders behind the iOS look. Text comes from food databases and an AI, so it must
always be escaped."""

from jim import ios


def test_text_is_escaped_everywhere():
    nasty = '<img src=x onerror=alert(1)> & "quotes"'
    html = ios.section([ios.row(nasty, subtitle=nasty, value=nasty, detail=nasty, leading=nasty, id=nasty)],
                       title=nasty, footnote=nasty, trailing=nasty) + ios.unavailable("x", nasty, nasty)
    assert "<img" not in html and "onerror=alert(1)>" not in html
    assert "&lt;img src=x onerror=alert(1)&gt; &amp;" in html
    assert 'data-id="&lt;img src=x onerror=alert(1)&gt; &amp; &quot;quotes&quot;"' in html


def test_rows_with_an_id_are_buttons_and_others_are_not():
    tappable = ios.row("Riverside Running", "21.10 km", id="42", icon_name="directions_run", tint="orange")
    assert tappable.startswith('<button class="ios-row has-icon" data-id="42">')
    assert "chevron_right" in tappable and "var(--ios-orange)" in tappable
    plain = ios.row("Distance", value="21.10 km")
    assert plain.startswith('<div class="ios-row">') and "chevron_right" not in plain
    assert "chevron_right" not in ios.row("Add Food", id="add", kind="action")     # actions have no arrow


def test_progress_bars_stay_within_bounds():
    assert "width:50.0%" in ios.progress(0.5, "green")
    assert "width:100.0%" in ios.progress(1.7, "red")       # over the goal still fills, never overflows
    assert "width:0.0%" in ios.progress(-1, "blue")


def test_sections_and_tabs():
    html = ios.section([ios.row("One"), ios.row("Two")], title="Sets", footnote="Note", trailing="2")
    assert html.count('class="ios-row"') == 2
    assert html.index("Sets") < html.index("One") < html.index("Note")


def test_simple_markdown_makes_bold_and_bullets():
    html = ios.simple_markdown("**Body metrics:**\n- Weight: **216 lb**\n- Sleep: 8.3 hours\n\nNice work.")
    assert html == ('<div><strong>Body metrics:</strong></div>'
                    '<ul style="margin:2px 0;padding-left:20px"><li>Weight: <strong>216 lb</strong></li>'
                    '<li>Sleep: 8.3 hours</li></ul>'
                    '<div style="height:6px"></div><div>Nice work.</div>')


def test_simple_markdown_escapes_everything_else():
    html = ios.simple_markdown('**<script>alert(1)</script>** and a lone ** star')
    assert "<script>" not in html
    assert html == '<div><strong>&lt;script&gt;alert(1)&lt;/script&gt;</strong> and a lone ** star</div>'


def test_simple_markdown_turns_headings_into_bold_lines():
    assert ios.simple_markdown("### Your week") == "<div><strong>Your week</strong></div>"
