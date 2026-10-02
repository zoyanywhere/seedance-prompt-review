"""Render the architecture SVG and PNG (developer tool; requires Pillow)."""
from pathlib import Path
from html import escape
import math
import os
from PIL import Image, ImageDraw, ImageFont

ROOT = Path(__file__).resolve().parents[1]
W, H = 1800, 2850
BG, CARD, FG, MUTED, ORANGE, TEAL = '#080808', '#171717', '#f5eee8', '#c1b7ae', '#ff6a25', '#7fccc1'
im = Image.new('RGB', (W,H), BG)
d = ImageDraw.Draw(im)
svg = [f'<svg xmlns="http://www.w3.org/2000/svg" width="{W}" height="{H}" viewBox="0 0 {W} {H}"><title>Promptlab: Film and Scene / Short and Ad production workflow</title><rect width="100%" height="100%" fill="{BG}"/>']
font_path = Path(os.environ.get('WINDIR','C:/Windows'))/'Fonts/segoeui.ttf'
if not font_path.exists(): font_path=Path('/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf')
def text(x,y,value,size=23,color=FG):
    font=ImageFont.truetype(str(font_path),size)
    d.text((x,y),value,font=font,fill=color)
    svg.append(f'<text x="{x}" y="{y+size}" font-family="Segoe UI,DejaVu Sans,sans-serif" font-size="{size}" fill="{color}">{escape(value)}</text>')
def box(x,y,w,h,tag,title,lines,accent=ORANGE):
    d.rounded_rectangle((x,y,x+w,y+h),radius=16,fill=CARD,outline=accent,width=2)
    svg.append(f'<rect x="{x}" y="{y}" width="{w}" height="{h}" rx="16" fill="{CARD}" stroke="{accent}" stroke-width="2"/>')
    text(x+23,y+15,tag,19,accent)
    text(x+23,y+44,title,28)
    for i,line in enumerate(lines): text(x+23,y+78+i*26,line,21,MUTED)
def arrow(points,color=ORANGE):
    d.line(points,fill=color,width=3)
    svg.append('<polyline points="'+' '.join(f'{x},{y}' for x,y in points)+f'" fill="none" stroke="{color}" stroke-width="3"/>')
    x,y=points[-1];a,b=points[-2];angle=math.atan2(y-b,x-a)
    tip=[(x,y),(x-15*math.cos(angle-.5),y-15*math.sin(angle-.5)),(x-15*math.cos(angle+.5),y-15*math.sin(angle+.5))]
    d.polygon(tip,fill=color)
    svg.append('<polygon points="'+' '.join(f'{x:.1f},{y:.1f}' for x,y in tip)+f'" fill="{color}"/>')

text(60,30,'ZOYA ANYWHERE / PROMPTLAB',20,ORANGE)
text(60,65,'One application. Two creative profiles.',48)
text(60,127,'Implemented workflow · project-scoped rules · five-block prompts · creator approval',24,MUTED)
box(60,190,810,140,'FILM & SCENE','Story, performance, continuity',['Dramaturgy, motivated camera language, emotional rhythm'])
box(930,190,810,140,'SHORT & AD','Hook, rhythm, payoff',['Opening clarity; message and product focus when relevant'])
arrow([(465,330),(465,355),(900,355),(900,380)])
arrow([(1335,330),(1335,355),(900,355),(900,380)])
box(240,380,1320,140,'CREATOR TEAM / INVITATION-ONLY','Brief + purpose + image / video / audio references',[
    'Project choices: audio, morphing, realism, style, camera; resolution and aspect ratio separately',
    'Both profiles allow creative rule breaks. The creator approves the proposed 4–30 second duration.'])
arrow([(900,520),(900,550)])
box(60,550,470,435,'SIX MASTER DOCUMENTS','Versioned production library',[
    '46 camera movements · 20 styles',
    'Matching sound profiles · camera looks',
    'Production planning · five blocks',
    '',
    'Curated, role-specific selections',
    'Creator intent outranks recipes',
    'No universal music / morphing ban',
    'Version included in review context',
    'Changed rules require a new plan',
    'Source observation stays independent'],TEAL)
box(580,550,1160,150,'SOURCE EVIDENCE / CONDITIONAL ON UPLOADED MEDIA','Gemini 3.8 Flash high → GPT-6.1 Sol high',[
    'Analyze actual images, video and audio; Sol cross-checks timestamped video frames.',
    'Preserve uncertainty. Cache observations by source identity and reference role.'],TEAL)
arrow([(1160,700),(1160,730)])
box(580,730,1160,110,'GPT-6 LUNA / MEDIUM','Organize the creator brief',['Intent, must-haves, uncertainties and source roles; preserve original requirements.'])
arrow([(530,800),(580,800)],TEAL)
arrow([(1160,840),(1160,870)])
box(580,870,1160,150,'CLAUDE OPUS 5.5 / HIGH','Direct the storyboard and timing',[
    'Select valid style / camera entries per shot; explain direct or recommended previs.',
    'Define initial / final states, continuous timing and visible / audible acceptance criteria.'])
arrow([(1160,1020),(1160,1050),(900,1050),(900,1080)])
box(240,1080,1320,110,'CREATOR CHECKPOINT','Review and approve storyboard + duration',[
    'Optional Gemini 3.1 Flash Image previews: creator approval required before review use.'],TEAL)
arrow([(900,1190),(900,1220)])
box(240,1220,1320,110,'GPT-6.1 SOL / HIGH','Draft the five-block prompt',[
    'Use approved shots, current production rules, source evidence and explicit media aliases.'])
text(60,1370,'PARALLEL SPECIALIST REVIEWS',21,ORANGE)
for x,model,title,lines in [
    (60,'GPT-6.1 SOL / HIGH','Action & timing',['Pacing, causality, readability','Hook / payoff fit to purpose']),
    (490,'GEMINI 3.8 FLASH / HIGH','Camera & visuals',['Framing, movement, style','Actual media and references']),
    (920,'GEMINI 3.8 FLASH / HIGH','Audio & dialogue',['Speech, Foley, atmosphere','Project music / silence rules']),
    (1350,'CLAUDE SONNET 5.5 / HIGH','Continuity',['Identity, state transitions','Reference and shot continuity'])]:
    arrow([(900,1330),(900,1400),(x+195,1400),(x+195,1415)])
    box(x,1415,390,160,model,title,lines)
    arrow([(x+195,1575),(x+195,1600),(900,1600),(900,1640)])
box(240,1640,1320,140,'GPT-6 ASTRA / HIGH','Repair supported findings and deterministic errors',[
    'Revise the actual prompt; preserve creator intent and approved reference authority.',
    'Do not silently erase blockers or repeat an unchanged prompt.'])
arrow([(900,1780),(900,1820)])
box(240,1820,1320,140,'CLAUDE OPUS 5.5 / HIGH','Independently verify the repaired prompt',[
    'Audit against source evidence, project rules and prior findings.',
    'Close major findings with evidence in the revision; otherwise keep them open.'])
arrow([(900,1960),(900,2000)])
box(240,2000,1320,140,'DETERMINISTIC GATE + AUDIT RESULT','Is the prompt ready for creator approval?',[
    'Five ordered nonempty blocks · valid media aliases · timing and project constraints',
    'A clean model verdict cannot bypass failed checks. Source conflicts stop approval.'],TEAL)
arrow([(240,2070),(80,2070),(80,1710),(240,1710)])
text(85,2170,'REPAIR LOOP',19,ORANGE)
text(85,2200,'Bounded by rounds,',18,MUTED)
text(85,2227,'budget and progress.',18,MUTED)
arrow([(900,2140),(900,2180)],TEAL)
box(320,2180,1240,110,'CREATOR DECISION','Approve the final prompt or revise the project',[
    'Unresolved blockers remain visible. A passing review cannot guarantee a generated video.'],TEAL)
arrow([(900,2290),(900,2330)],TEAL)
box(240,2330,1320,190,'MANUAL HANDOFF / VIDEO GENERATION IS OUTSIDE PROMPTLAB','Final prompt + reference mapping + separate settings',[
    '1  Scene context & cinematography                 2  Active references & binding',
    '3  Action beats & timing                                  4  Locks & constraints',
    '5  Animation & physics',
    '@Image1 / @Video1 / @Audio1 → actual file names; duration / ratio / resolution separately'])
box(60,2570,810,170,'OPERATIONS','Usage, isolation and compatibility',[
    'Token / cost ledger includes previews and failed-call reserves.',
    'Project ownership and versioned checkpoints isolate contexts.',
    'Existing projects keep their workflow until a profile is selected.'],TEAL)
box(930,2570,810,170,'PLANNING ADVICE / NOT EXECUTED HERE','Previs, hero-shot tests and post-production',[
    'Recommendations support difficult spatial / physical actions.',
    'No automatic Blender, video rendering, lip-sync or compositing.',
    'No promise of exact visual reproduction or virality.'],TEAL)
text(60,2790,'MODEL DEFAULTS · Configurable per deployment · Production profiles / 2026-10-03',20,MUTED)
svg.append('</svg>')
(ROOT/'agent-architecture-en.svg').write_text('\n'.join(svg),encoding='utf-8')
im.save(ROOT/'agent-architecture-en.png')
print('Rendered architecture SVG and PNG')
