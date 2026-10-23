# Generates assets/eks-cap-upgrade-timeline.svg from results-ops/probes.jsonl
import json, sys
root = sys.argv[1]
probes = [json.loads(l) for l in open(f"{root}/assets/data/eks-capabilities-2026-10/results-ops/probes.jsonl")]
def sec(hms):
    h, m, s = map(int, hms.split(":")); return h*3600 + m*60 + s
T0, T1 = sec("02:47:50"), sec("02:58:20")
X0, X1 = 170, 1080
def x(t): return X0 + (t - T0) * (X1 - X0) / (T1 - T0)
up_req, flap0, active = sec("02:48:48"), sec("02:52:41"), sec("02:55:52")
oos = sec("02:53:38")
upg = [p for p in probes if p["label"].startswith(("before", "during"))]

o = []
a = o.append
a('<svg xmlns="http://www.w3.org/2000/svg" width="1120" height="700" viewBox="0 0 1120 700" role="img" aria-labelledby="title desc">')
a('  <title id="title">EKS 1.36에서 1.37로 in-place 업그레이드하는 동안의 capability 측정 타임라인</title>')
a('  <desc id="desc">02:48:48에 업그레이드를 요청했고 02:55:52에 ACTIVE가 됐다. 02:52:41부터 02:55:52까지 API 서버 버전이 1.36.4와 1.37.0 사이를 오갔다. 세 capability는 내내 ACTIVE였고 버전과 health가 바뀌지 않았다. Argo CD self-heal은 2초, 6초 뒤 113초, 254초로 늘어났는데 업그레이드가 아니라 self-heal 백오프 때문이었다. kro 반영은 약 1초, ACK Queue 동기화는 1.5~3.5초였고, API 버전이 오가던 3분 동안에는 kro와 ACK 측정이 없었다.</desc>')
a('  <defs><pattern id="hatch" width="8" height="8" patternUnits="userSpaceOnUse" patternTransform="rotate(45)"><rect width="8" height="8" fill="#fbe9e7"/><line x1="0" y1="0" x2="0" y2="8" stroke="#e7b9b1" stroke-width="3"/></pattern></defs>')
a('  <rect width="1120" height="700" rx="20" fill="#fbfaf7"/>')
a('  <g font-family="Noto Sans CJK KR, sans-serif">')
a('    <text x="42" y="49" fill="#5f6670" font-size="18" font-weight="600" letter-spacing="2">IN-PLACE UPGRADE · 2026-10-22 UTC</text>')
a('    <text x="42" y="93" fill="#222e3b" font-size="31" font-weight="700">업그레이드 7분 동안 capability는 그대로였습니다</text>')

# flapping band
a(f'    <rect x="{x(flap0):.1f}" y="128" width="{x(active)-x(flap0):.1f}" height="352" fill="url(#hatch)" opacity="0.75"/>')
a(f'    <text x="{(x(flap0)+x(active))/2:.1f}" y="146" text-anchor="middle" fill="#8a2c20" font-size="14" font-weight="700">API /version 1.36.4 ↔ 1.37.0</text>')

lanes = [("클러스터", 180), ("capability 3개", 240), ("Argo CD self-heal", 310), ("kro 반영", 380), ("ACK Queue 동기화", 440)]
for name, y in lanes:
    a(f'    <text x="42" y="{y+6}" fill="#26394b" font-size="16" font-weight="600">{name}</text>')
    a(f'    <line x1="{X0}" y1="{y}" x2="{X1}" y2="{y}" stroke="#e0ddd5"/>')

# cluster lane
y = 180
a(f'    <rect x="{X0}" y="{y-14}" width="{x(up_req)-X0:.1f}" height="28" rx="6" fill="#e7f4ef" stroke="#3c7c68"/>')
a(f'    <rect x="{x(up_req):.1f}" y="{y-14}" width="{x(active)-x(up_req):.1f}" height="28" rx="6" fill="#fff4df" stroke="#b88632"/>')
a(f'    <text x="{(x(up_req)+x(flap0))/2:.1f}" y="{y+5}" text-anchor="middle" fill="#654814" font-size="14" font-weight="600">UPDATING (02:48:48 요청)</text>')
a(f'    <rect x="{x(active):.1f}" y="{y-14}" width="{X1-x(active):.1f}" height="28" rx="6" fill="#e7f4ef" stroke="#3c7c68"/>')
a(f'    <text x="{(x(active)+X1)/2:.1f}" y="{y+5}" text-anchor="middle" fill="#245c4b" font-size="14" font-weight="600">ACTIVE 1.37</text>')
a(f'    <text x="{(X0+x(up_req))/2:.1f}" y="{y+5}" text-anchor="middle" fill="#245c4b" font-size="14">1.36</text>')

# capability lane
y = 240
a(f'    <rect x="{X0}" y="{y-14}" width="{X1-X0}" height="28" rx="6" fill="#e3ecf8" stroke="#4a6fa5"/>')
a(f'    <text x="{(X0+X1)/2:.1f}" y="{y+5}" text-anchor="middle" fill="#1f3b63" font-size="14" font-weight="600">Argo CD · kro · ACK 모두 ACTIVE, 버전 · health · modifiedAt 변화 없음</text>')

# argocd lane
y = 310
for p in upg:
    s = sec(p["start_utc"]); d = p["argocd_selfheal_s"]
    w = max(x(s+d) - x(s), 3)
    a(f'    <rect x="{x(s):.1f}" y="{y-11}" width="{w:.1f}" height="22" rx="4" fill="#cfdcef" stroke="#4a6fa5"/>')
    if p["label"].startswith("during"):
        lx = x(s) + (w/2 if w > 60 else 0)
        anchor = "middle" if w > 60 else "start"
        a(f'    <text x="{lx:.1f}" y="{y-18}" text-anchor="{anchor}" fill="#1f3b63" font-size="14" font-weight="600">{d:.1f}초</text>')
a(f'    <circle cx="{x(oos):.1f}" cy="{y}" r="6" fill="#ffffff" stroke="#8a2c20" stroke-width="2"/>')
a(f'    <text x="{x(oos)+10:.1f}" y="{y+28}" fill="#8a2c20" font-size="14">02:53:38 OutOfSync 감지</text>')
a(f'    <text x="{X0}" y="{y+28}" fill="#5f6670" font-size="14">전 3회 2.1~14.6초</text>')

# kro + ack ticks: ack time from queue name, kro just before
for name_y, key in ((380, "kro_propagate_s"), (440, "ack_queue_synced_s")):
    for p in upg:
        q = p["queue"].split("-")[1]
        t = sec(f"{q[:2]}:{q[2:4]}:{q[4:]}") - (2 if key.startswith("kro") else 0)
        a(f'    <rect x="{x(t)-2:.1f}" y="{name_y-11}" width="5" height="22" rx="2" fill="#3c7c68"/>')
a(f'    <text x="{x(sec("02:53:10")):.1f}" y="{380+5}" fill="#8a2c20" font-size="14" font-weight="600">측정 없음</text>')
a(f'    <text x="{x(sec("02:53:10")):.1f}" y="{440+5}" fill="#8a2c20" font-size="14" font-weight="600">측정 없음</text>')
a(f'    <text x="{X1}" y="{380-16}" text-anchor="end" fill="#245c4b" font-size="14">전후 1.02~1.07초</text>')
a(f'    <text x="{X1}" y="{440-16}" text-anchor="end" fill="#245c4b" font-size="14">전후 1.49~3.53초</text>')

# axis
for m in range(48, 59):
    t = sec(f"02:{m}:00")
    a(f'    <line x1="{x(t):.1f}" y1="470" x2="{x(t):.1f}" y2="478" stroke="#8a929b"/>')
    a(f'    <text x="{x(t):.1f}" y="496" text-anchor="middle" fill="#5f6670" font-size="14">02:{m}</text>')
a(f'    <line x1="{X0}" y1="470" x2="{X1}" y2="470" stroke="#8a929b"/>')

# backoff panel
a('    <rect x="42" y="526" width="1038" height="140" rx="14" fill="#ffffff" stroke="#c4cbd1"/>')
a('    <text x="62" y="557" fill="#26394b" font-size="17" font-weight="700">self-heal이 늦어진 이유: upstream Argo CD의 백오프</text>')
a('    <text x="62" y="582" fill="#5f6670" font-size="14">같은 앱에서 self-heal이 반복되면 다음 시도까지 2초에서 3배씩 늘고 300초를 넘지 않음. 업그레이드 뒤 대조 측정도 251~252초</text>')
steps = ["2초", "6초", "18초", "54초", "162초", "300초 (상한)"]
bx = 62
for i, s in enumerate(steps):
    w = 105 if i < 5 else 150
    fill, stroke, col = ("#e3ecf8", "#4a6fa5", "#1f3b63") if i < 5 else ("#fbe9e7", "#b5483a", "#8a2c20")
    a(f'    <rect x="{bx}" y="600" width="{w}" height="44" rx="10" fill="{fill}" stroke="{stroke}"/>')
    a(f'    <text x="{bx+w/2}" y="628" text-anchor="middle" fill="{col}" font-size="16" font-weight="700">{s}</text>')
    if i < 5:
        a(f'    <text x="{bx+w+15}" y="628" text-anchor="middle" fill="#8a929b" font-size="15">×3</text>')
    bx += w + 30
a(f'    <text x="{bx-14}" y="620" fill="#5f6670" font-size="14">관리형은 직접</text>')
a(f'    <text x="{bx-14}" y="640" fill="#5f6670" font-size="14">조정 못 함 · Support 요청</text>')

a('    <text x="1078" y="690" text-anchor="end" fill="#8a929b" font-size="14">측정 1회 = Argo CD self-heal → kro 반영 → ACK Queue 생성 순서. 막대와 눈금 위치는 기록 시각 기준 근사</text>')
a('  </g>')
a('</svg>')
open(f"{root}/assets/eks-cap-upgrade-timeline.svg", "w").write("\n".join(o) + "\n")
