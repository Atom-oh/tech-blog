#!/usr/bin/env python3
"""Generate the two Korean, silent, explicitly simulated HyperFrames demos."""

import html
import re
from pathlib import Path

BASE = Path(__file__).resolve().parent
PHASES = [(0, 8), (8, 10), (18, 10), (28, 10), (38, 12)]

STORIES = {
    "manual": [
        ("먼저 Writer와 다른 AZ의 Reader를 확인합니다", "애플리케이션은 Writer Endpoint로 접속합니다.", "normal",
         "Writer", "Reader · tier 0", "SQL 쓰기 처리", "다른 AZ의 승격 후보"),
        ("운영자가 승격할 Reader를 지정합니다", "CLI 요청은 전환의 시작입니다. 완료 판정은 별도로 합니다.", "terminal",
         "Writer", "Reader · tier 0", "SQL 쓰기 처리", "지정한 대상: demo-b"),
        ("Reader가 Writer로 승격됩니다", "전환 중 기존 연결과 쓰기 요청은 영향을 받을 수 있습니다.", "switch",
         "역할 전환 중", "새 Writer", "기존 연결 종료 가능", "demo-b 승격"),
        ("같은 Endpoint로 새 Writer에 재접속합니다", "Endpoint 이름은 유지되지만 대상과 DNS 응답은 바뀝니다.", "reconnect",
         "Reader 복귀 가능", "Writer", "정상 복귀한 경우", "새 연결로 쓰기 처리"),
        ("완료 기준은 역할 변경과 쓰기 성공입니다", "SELECT 성공만으로는 업무 쓰기 복구를 확인할 수 없습니다.", "summary",
         "Reader", "Writer", "정상 복귀한 예시", "INSERT + 조회 확인"),
    ],
    "automatic": [
        ("자동 복구도 준비된 다른 AZ의 Reader를 활용합니다", "AZ A의 Writer와 AZ B의 Reader가 있는 예시입니다.", "normal",
         "Writer", "Reader · tier 0", "SQL 쓰기 처리", "다른 AZ의 승격 후보"),
        ("Aurora가 Writer의 문제를 감지합니다", "항상 다른 인스턴스로 전환하는 것은 아닙니다.", "failure",
         "Writer 장애", "Reader · tier 0", "연결·쓰기 실패 가능", "사용 가능한 후보"),
        ("Failover 경로에서 Reader를 승격합니다", "복구 경로는 재시작 또는 전환 등 상태에 따라 결정됩니다.", "switch",
         "사용 불가", "새 Writer", "장애 인스턴스", "후보 상태와 tier 고려"),
        ("애플리케이션이 새 Writer로 연결합니다", "Aurora의 역할 변경 뒤에도 앱의 재접속 처리가 필요합니다.", "reconnect",
         "복구 상태 확인", "Writer", "복귀 시점은 별도 확인", "새 연결로 쓰기 재개"),
        ("자동 전환도 쓰기 복구까지 확인합니다", "이 영상은 장애 감지와 복구 시간을 측정한 결과가 아닙니다.", "summary",
         "상태 확인", "Writer", "이전 인스턴스 확인", "INSERT + 조회 확인"),
    ],
}

CSS = """
@font-face{font-family:DemoKR;src:url('assets/demo-kr.woff') format('woff');font-weight:400;font-display:block}
*{box-sizing:border-box}html,body{margin:0;width:1280px;height:720px;overflow:hidden;background:#0b1220;color:#f2f5fa}
body{font-family:DemoKR,sans-serif}
#aurora-main{width:100%;height:100%;position:relative;overflow:hidden}
.clip{position:absolute;inset:0;background:#0b1220}
.topline{position:absolute;left:64px;right:64px;top:80px;display:flex;justify-content:space-between;align-items:center;font-size:19px;color:#b8c8dc}
.badge{border:1px solid #5b718c;border-radius:20px;padding:5px 15px;color:#e7eef8;background:#18263a}
.heading{position:absolute;left:64px;top:125px;margin:0;font-size:38px;font-weight:700;line-height:1.3;letter-spacing:-1.1px}
.subtitle{position:absolute;left:64px;top:184px;margin:0;font-size:24px;color:#c3cfdf}
.diagram{position:absolute;left:0;top:0;width:1280px;height:720px}
.app,.endpoint,.node,.az-label,.storage,.notice,.check{position:absolute}
.app{left:70px;top:271px;width:230px;height:83px;display:grid;place-items:center;border:1px solid #7f91a8;border-radius:13px;background:#1a2b40;font-size:27px}
.endpoint{left:70px;top:391px;width:230px;height:70px;display:grid;place-items:center;border:1px solid #7f91a8;border-radius:12px;background:#162236;font-size:23px}
.az{position:absolute;top:229px;width:344px;height:285px;border:1.5px dashed #698099;border-radius:15px;background:#101c2d}
.az-a{left:363px}.az-b{left:801px}.az-label{top:17px;left:23px;font-size:22px;color:#c3cfdf}
.node{top:76px;left:25px;width:293px;height:158px;border:2px solid #b7a07c;border-radius:14px;background:#352e26;display:flex;flex-direction:column;align-items:center;justify-content:center;gap:12px}
.node.reader{border-color:#70b6a3;background:#14352f}.node.failed{border-color:#ec8d90;background:#3e222c}
.role{font-size:27px;font-weight:700}.id{font:22px DemoKR,sans-serif;color:#e6ebf2}.node-note{font-size:19px;color:#e0e8f2}
.storage{left:70px;top:549px;width:1075px;height:63px;border:1px solid #66778e;border-radius:12px;background:#172239;display:flex;align-items:center;justify-content:center;font-size:23px;color:#dbe6f3;gap:17px}
.storage strong{color:#c7c0ff}.storage .pill{border-left:1px solid #66778e;padding-left:19px}
.notice{left:368px;top:525px;font-size:18px;color:#e8bac1}
.caption{position:absolute;left:64px;right:64px;bottom:43px;border-top:1px solid #35475e;padding-top:12px;font-size:22px;color:#e7edf6;display:flex;justify-content:space-between;gap:20px}
.caption span{color:#bdcadd;font-size:18px}.page{font-size:20px;color:#c8d4e3}
.check{left:76px;top:236px;width:1128px}
.check-row{display:flex;align-items:center;gap:28px;padding:24px 28px;margin-bottom:15px;background:#18283b;border:1px solid #4f647c;border-radius:13px;font-size:28px}
.check-num{width:46px;height:46px;display:grid;place-items:center;border-radius:50%;border:1px solid #8bb7d7;color:#d3e6f6;font-size:24px}
.check-row small{display:block;color:#bfcde0;font-size:21px;margin-top:6px}
.terminal-host{position:absolute;left:64px;top:230px;width:1152px;height:382px;--ctr-font-size:23px;--font-mono:DemoKR,monospace;--surface:#152438;--border:#748aa4;--muted:#b8c8dc;--bg:#0b1220;--fg:#f1f5fa}
.failure-label{position:absolute;left:70px;top:490px;font-size:22px;color:#f5b0b4}
.paths{position:absolute;inset:0;width:1280px;height:720px;overflow:visible}
.route{stroke:#a2c9ed;stroke-width:3;fill:none;stroke-linecap:round;stroke-linejoin:round}
.hold-line{stroke:#536881;stroke-width:1.7;fill:none}
.failure-mark{font-size:33px;position:absolute;left:670px;top:300px;color:#f7b2b8}
"""


def diagram(kind, role_a, role_b, note_a, note_b, automatic):
    reconnect = kind in ("reconnect",)
    failed = automatic and kind in ("failure", "switch", "reconnect")
    a_cls = "failed" if failed else ("reader" if reconnect else "")
    b_cls = "" if kind in ("switch", "reconnect") else "reader"
    if reconnect:
        route = "M300 426H331V219H783V383H826"
    else:
        route = "M300 426H333V383H388"
    extra = ""
    if kind in ("switch", "failure"):
        extra = '<div class="failure-label">연결 오류 → 제한된 재시도</div>'
    if failed:
        extra += '<div class="failure-mark">×</div>'
    return f"""
<div class="diagram">
<svg class="paths" viewBox="0 0 1280 720" aria-hidden="true">
<defs><marker id="arrow-{kind}" markerWidth="10" markerHeight="10" refX="7" refY="4" orient="auto"><path d="M0 0 8 4 0 8" fill="none" stroke="#a2c9ed" stroke-width="1.8"/></marker></defs>
<path class="hold-line" d="M185 354V391"/>
<path class="hold-line" d="M535 514V549M973 514V549"/>
<path class="route" d="{route}" marker-end="url(#arrow-{kind})"/>
</svg>
<div class="app">애플리케이션</div><div class="endpoint">Writer Endpoint</div>
<div class="az az-a"><span class="az-label">AZ A</span><div class="node {a_cls}"><span class="role">{html.escape(role_a)}</span><span class="id">demo-a</span><span class="node-note">{html.escape(note_a)}</span></div></div>
<div class="az az-b"><span class="az-label">AZ B</span><div class="node {b_cls}"><span class="role">{html.escape(role_b)}</span><span class="id">demo-b</span><span class="node-note">{html.escape(note_b)}</span></div></div>
<div class="storage"><strong>공유 클러스터 볼륨</strong><span class="pill">스토리지 복사본 6개 · 3개 AZ</span></div>
{extra}</div>"""


def summary(automatic):
    rows = (
        [
            ("다른 AZ의 Reader", "사용 가능한 승격 후보와 용량을 준비합니다."),
            ("Writer 역할 + 실제 쓰기", "새 Writer에서 INSERT 후 조회까지 확인합니다."),
            ("자동 복구의 범위 구분", "수동 전환·crash 쿼리로 자연 장애 감지를 입증하지 않습니다."),
        ] if automatic else [
            ("지정한 Reader가 Writer인지", "DBClusterMembers의 IsClusterWriter를 확인합니다."),
            ("새 연결에서 INSERT 성공", "실제 앱의 재접속과 쓰기 연속 성공도 확인합니다."),
            ("전환 연습과 실측을 구분", "이 영상은 동작 설명이며 복구 시간 측정이 아닙니다."),
        ]
    )
    return '<div class="check">' + "".join(
        f'<div class="check-row"><span class="check-num">{i}</span><div>{title}<small>{desc}</small></div></div>'
        for i, (title, desc) in enumerate(rows, 1)
    ) + "</div>"


def make(mode):
    automatic = mode == "automatic"
    label = "자동 Failover" if automatic else "수동 Failover"
    scenes = []
    captions = []
    for i, ((start, duration), values) in enumerate(zip(PHASES, STORIES[mode]), 1):
        title, subtitle, kind, role_a, role_b, note_a, note_b = values
        if kind == "terminal":
            body = """<div id="terminal-demo" class="clip terminal-host" data-composition-id="code-terminal-run"
 data-composition-src="compositions/components/code-terminal-run.html" data-start="8" data-duration="10"
 data-track-index="2" data-width="1152" data-height="382"></div>"""
        elif kind == "summary":
            body = summary(automatic)
        else:
            body = diagram(kind, role_a, role_b, note_a, note_b, automatic)
        # Timed child host is a sibling of scene clips, never nested in another timed clip.
        terminal_host = body if kind == "terminal" else ""
        if terminal_host:
            body = ""
        scenes.append(f"""
<section id="scene-{i}" class="clip" data-start="{start}" data-duration="{duration}" data-track-index="1">
<div class="topline"><span>AURORA MYSQL / {label.upper()}</span><span class="badge">동작 시뮬레이션 · 실제 녹화 아님</span></div>
<h1 class="heading">{html.escape(title)}</h1><p class="subtitle">{html.escape(subtitle)}</p>
{body}
<div class="caption">한 리전 내부의 Writer 전환<span>재생 시간 ≠ Failover 소요 시간</span><b class="page">{i:02d} / 05</b></div>
</section>{terminal_host}""")
        captions.append(f"{i}\n00:00:{start:02d}.000 --> 00:00:{start + duration:02d}.000 line:0 position:50% align:center size:90%\n{title}\n")
    # End 50 s is valid with this fixed storyboard.
    vtt = "WEBVTT\n\n" + "\n".join(captions)
    (BASE / f"{mode}.ko.vtt").write_text(vtt, encoding="utf-8")
    source = f"""<!doctype html>
<html lang="ko"><head><meta charset="UTF-8"><meta name="viewport" content="width=1280, height=720">
<title>Aurora MySQL {label} — 동작 시뮬레이션</title>
<script src="assets/gsap.min.js"></script><style>{CSS}</style></head><body>
<div id="aurora-main" data-composition-id="aurora-main" data-start="0" data-width="1280" data-height="720" data-duration="50" data-fps="24">
{''.join(scenes)}
</div><script>
const tl = gsap.timeline({{paused:true}});
document.querySelectorAll('section.clip').forEach((scene) => {{
 const start = Number(scene.dataset.start);
 const children = scene.querySelectorAll('.heading, .subtitle, .app, .endpoint, .az, .storage, .check-row, .failure-label, .failure-mark');
 tl.fromTo(children, {{opacity:0, y:12}}, {{opacity:1, y:0, duration:0.55, stagger:0.06, ease:'power3.out'}}, start + 0.1);
 scene.querySelectorAll('.route').forEach((path) => {{
  const length = path.getTotalLength();
  path.style.strokeDasharray = String(length);
  path.style.strokeDashoffset = String(length);
  tl.fromTo(path, {{strokeDashoffset:length}}, {{strokeDashoffset:0,duration:1.4,ease:'power2.out'}}, start + 0.7);
 }});
}});
window.__timelines['aurora-main'] = tl;
</script></body></html>"""
    (BASE / mode / "index.html").write_text(source, encoding="utf-8")
    (BASE / mode / "BRIEF.md").write_text(f"""---
workflow: general-video
flow: automation
storyboard: no
message: "Aurora MySQL의 {label}와 전환 후 쓰기 복구"
destination: blog-embed
aspect: "16:9"
language: ko
audience: AWS operators and backend developers
length: 50s
---

## Intent
사용자 요청: Aurora MySQL 수동 Failover 동작을 데모 영상과 함께 설명하고 자동 Failover, 사용 시점, Multi-AZ 가용성을 비교하는 한국어 블로그.

## Production decisions
설명용 시뮬레이션, 1280×720, 24fps, 무음, 한국어 화면 설명과 WebVTT 자막.
실제 AWS 녹화·실측이 아니라는 표시를 모든 장면에 유지한다.
수동·자동 영상은 같은 인스턴스 예시와 레이아웃을 사용한다.
AWS 클러스터를 생성하거나 장애 작업을 실행하지 않는다.
CLI와 설치된 core/general-video 스킬은 0.8.181 환경을 사용하며 전역 스킬 변경은 생략한다.

## Storyboard
0–8초: 정상 Writer·Reader 구성.
8–18초: {'장애 감지' if automatic else 'Reader를 지정한 CLI 요청'}.
18–28초: Reader 승격 경로.
28–38초: Endpoint를 통한 새 연결.
38–50초: 역할 변경과 실제 쓰기 확인.

## Assets
GSAP 3.14.2 로컬 파일, Noto Sans CJK KR의 글리프 subset.
{'직접 작성한 역할·Endpoint 도식.' if automatic else '공식 registry의 code-terminal-run 컴포넌트에 실습 명령을 적용.'}

## Motion
짧은 opacity·y 진입과 SVG path draw. 단일 paused timeline, seek-safe.
모든 화면 상태·시간은 설명용이며 서비스 이벤트 기록을 모사한 실측 결과가 아니다.
""", encoding="utf-8")


if __name__ == "__main__":
    for name in STORIES:
        make(name)
