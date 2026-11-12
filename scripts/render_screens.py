#!/usr/bin/env python3
"""Regenerate the terminal screenshots in docs/assets (SVG, English and Korean)."""
from __future__ import annotations

from html import escape
from pathlib import Path

OUT = Path(__file__).resolve().parent.parent / "docs" / "assets"
FONT = ("ui-monospace, SFMono-Regular, Menlo, Consolas, 'D2Coding', 'Noto Sans Mono CJK KR', "
        "'Apple SD Gothic Neo', 'Malgun Gothic', monospace")
C = {"dim": "#64748b", "label": "#94a3b8", "text": "#e2e8f0", "white": "#f8fafc", "q": "#60a5fa",
     "sel": "#22d3ee", "amber": "#fbbf24", "ok": "#4ade80"}
Span = tuple  # (text, colour key, bold)
TAB = "\t"
TAB_X = 160


def screen(name: str, title: str, lines: list[list[Span]], width: int = 980) -> None:
    line_h, top, pad = 25, 56, 28
    height = top + pad + line_h * len(lines) + 12
    def tspans(spans: list[Span]) -> str:
        out = []
        for text, colour, bold in spans:
            weight = ' font-weight="700"' if bold else ""
            out.append(f'<tspan fill="{C[colour]}"{weight}>{escape(text)}</tspan>')
        return "".join(out)

    rows = []
    for i, spans in enumerate(lines):
        y = top + pad + i * line_h
        # A TAB span starts a second column at a fixed x, so labels of any script line up.
        cut = next((k for k, sp in enumerate(spans) if sp[0] == TAB), None)
        if cut is None:
            rows.append(f'<text x="28" y="{y}" xml:space="preserve">{tspans(spans)}</text>')
        else:
            rows.append(f'<text x="28" y="{y}" xml:space="preserve">{tspans(spans[:cut])}</text>')
            rows.append(f'<text x="{TAB_X}" y="{y}" xml:space="preserve">{tspans(spans[cut + 1:])}</text>')
    svg = f'''<svg xmlns="http://www.w3.org/2000/svg" width="{width}" height="{height}" viewBox="0 0 {width} {height}" role="img" aria-label="{escape(title)}">
  <rect width="{width}" height="{height}" rx="14" fill="#0b1220" stroke="#1f2a3d"/>
  <rect width="{width}" height="40" rx="14" fill="#111a2e"/>
  <rect y="26" width="{width}" height="14" fill="#111a2e"/>
  <circle cx="24" cy="20" r="6.5" fill="#ff5f57"/><circle cx="46" cy="20" r="6.5" fill="#febc2e"/><circle cx="68" cy="20" r="6.5" fill="#28c840"/>
  <text x="{width // 2}" y="25" text-anchor="middle" font-family="{FONT}" font-size="13" fill="#64748b">{escape(title)}</text>
  <g font-family="{FONT}" font-size="15">
    {chr(10).join("    " + r for r in rows).strip()}
  </g>
</svg>
'''
    (OUT / name).write_text(svg, encoding="utf-8")


def menu(lang: str) -> list[list[Span]]:
    if lang == "en":
        status = [("Camera:", "label", False), ("Import to:", "label", False),
                  ("Time zone:", "label", False), ("Camera clock:", "label", False)]
        values = ["Vugera VG-701V3", "~/Downloads/Dashcam", "America/Toronto (EST)"]
        clock = [("14 d 9 h 18 m 36 s behind real time", "amber", False), (" (calibrated 2025-03-15 17:30)", "dim", False)]
        question = [("? ", "q", True), ("What would you like to do? ", "white", True),
                    ("(Up/Down to move, Enter to choose, Esc to go back)", "dim", False)]
        items = ["Import videos from the SD card", "Calibrate the camera clock", "Prepare (format) an SD card",
                 "Settings", "Quit"]
    else:
        status = [("카메라:", "label", False), ("저장 위치:", "label", False),
                  ("시간대:", "label", False), ("카메라 시계:", "label", False)]
        values = ["Vugera VG-701V3", "~/Downloads/Dashcam", "Asia/Seoul (KST)"]
        clock = [("실제 시각보다 14일 9시간 18분 36초 느림", "amber", False), (" (2025-03-15 17:30 보정)", "dim", False)]
        question = [("? ", "q", True), ("무엇을 하시겠습니까? ", "white", True),
                    ("(위/아래로 이동, Enter로 선택, Esc로 뒤로)", "dim", False)]
        items = ["SD 카드에서 영상 가져오기", "카메라 시계 보정", "SD 카드 준비 (포맷)", "설정", "종료"]
    lines: list[list[Span]] = [[("dashcam-sdprep 0.2.0", "dim", False)], []]
    for (label, colour, bold), value in zip(status[:3], values):
        lines.append([(label, colour, bold), (TAB, "text", False), (value, "text", False)])
    lines.append([status[3], (TAB, "text", False), *clock])
    lines += [[], question, [(" » " + items[0], "sel", True)]]
    lines += [[("   " + item, "text", False)] for item in items[1:]]
    return lines


def importing(lang: str) -> list[list[Span]]:
    names = ["2025-03-15_08-02-11_REC1.avi", "2025-03-15_08-03-27_REC1.avi", "2025-03-15_08-04-55_EVT1.avi"]
    if lang == "en":
        head = [[("Found 28 videos, real time 2025-03-15 08:02:11 EST to 2025-03-15 18:44:30 EST.", "text", False)],
                [("They will be copied to ~/Downloads/Dashcam, one folder per day, with subtitle files.", "text", False)],
                [("? ", "q", True), ("Start importing? ", "white", True), ("Yes", "sel", False)]]
        done = "imported  "
        tail = [[("Done: 28 imported, 0 already imported.", "ok", True)],
                [("? ", "q", True), ("Eject the card now? ", "white", True), ("Yes", "sel", False)],
                [("The card is ejected. You can remove it.", "text", False)]]
    else:
        head = [[("영상 28개를 찾았습니다. 실제 시각 2025-03-15 08:02:11 KST ~ 2025-03-15 18:44:30 KST.", "text", False)],
                [("~/Downloads/Dashcam에 날짜별 폴더로 자막 파일과 함께 복사합니다.", "text", False)],
                [("? ", "q", True), ("가져오기를 시작할까요? ", "white", True), ("Yes", "sel", False)]]
        done = "가져옴  "
        tail = [[("완료: 28개 가져옴, 0개는 이미 있음.", "ok", True)],
                [("? ", "q", True), ("지금 카드를 꺼낼까요? ", "white", True), ("Yes", "sel", False)],
                [("카드를 꺼냈습니다. 이제 빼셔도 됩니다.", "text", False)]]
    rows = [[(f"[{i}/28] ", "dim", False), (done, "ok", False), (n, "text", False)] for i, n in enumerate(names, 1)]
    rows.append([("   ...", "dim", False)])
    rows.append([("[28/28] ", "dim", False), (done, "ok", False), ("2025-03-15_18-44-30_MOT1.avi", "text", False)])
    return head + rows + tail


if __name__ == "__main__":
    OUT.mkdir(parents=True, exist_ok=True)
    for lang in ("en", "ko"):
        screen(f"menu-{lang}.svg", "dashcam-sdprep", menu(lang))
        screen(f"import-{lang}.svg", "dashcam-sdprep: import", importing(lang))
    print("wrote", ", ".join(sorted(p.name for p in OUT.glob("*.svg"))))
