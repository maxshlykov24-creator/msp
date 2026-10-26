from pathlib import Path
from fontTools.ttLib import TTFont
from fontTools.pens.svgPathPen import SVGPathPen
import base64,json,shutil
root=Path(__file__).resolve().parent.parent
for d in ['svg','png','webp','icons','backgrounds']: (root/d).mkdir(exist_ok=True)
font=TTFont('/System/Library/Fonts/HelveticaNeue.ttc',fontNumber=10)
glyphs=font.getGlyphSet(); cmap=font.getBestCmap(); upm=font['head'].unitsPerEm
pen=SVGPathPen(glyphs)
x=0; parts=[]
for ch in 'MS Product':
 name=cmap[ord(ch)]; pen=SVGPathPen(glyphs); glyphs[name].draw(pen)
 parts.append(f'<path transform="translate({x} 0)" d="{pen.getCommands()}"/>')
 x+=glyphs[name].width-12
scale=72/upm
textwidth=x*scale
# Geometric reconstruction of the existing MS monogram, preserving its three ribbons.
paths=['M 0 220 L 400 24 L 870 242 L 446 480 L 444 1024 L 362 982 L 362 404 L 710 232 L 595 180 L 252 354 L 252 926 L 170 886 L 170 304 L 508 138 L 412 94 L 80 262 L 78 834 L 0 794 Z','M 522 458 L 884 280 L 884 392 L 602 532 L 600 636 L 884 498 L 884 844 L 522 1024 L 524 912 L 804 774 L 804 648 L 516 790 Z']
def mark(fill): return '<g fill="'+fill+'">'+''.join('<path d="'+p+'"/>' for p in paths)+'</g>'
def word(fill): return '<g fill="'+fill+'" transform="scale('+str(scale)+' '+str(-scale)+')">'+''.join(parts)+'</g>'
def defs(): return '<defs><linearGradient id="brand" x1="0" y1="0" x2="1" y2=".6"><stop stop-color="#bc2aff"/><stop offset=".55" stop-color="#8557ff"/><stop offset="1" stop-color="#535dff"/></linearGradient></defs>'
def svg(w,h,body):return f'<svg xmlns="http://www.w3.org/2000/svg" width="{w}" height="{h}" viewBox="0 0 {w} {h}" role="img" aria-label="MS Product">'+defs()+body+'</svg>'
def lock(markfill,textfill):return '<g transform="translate(16 8) scale(.1015625)">'+mark(markfill)+'</g><g transform="translate(134 85)">'+word(textfill)+'</g>'
w=round(150+textwidth);h=120
variants={'gradient-dark':('url(#brand)','#17131f'),'gradient-light':('url(#brand)','#f7f5ff'),'white':('#ffffff','#ffffff'),'black':('#17131f','#17131f'),'violet':('#7546df','#7546df'),'full-gradient':('url(#brand)','url(#brand)')}
for name,(m,t) in variants.items():
 (root/'svg'/f'logo-horizontal-{name}.svg').write_text(svg(w,h,lock(m,t)))
 (root/'svg'/f'mark-{name}.svg').write_text(svg(1040,1160,'<g transform="translate(78 68)">'+mark(m)+'</g>'))
for name,bg,v in [('dark','#07060b','gradient-light'),('light','#f8f7fc','gradient-dark'),('violet','#7546df','white')]:
 body=f'<rect width="1200" height="630" fill="{bg}"/><g transform="translate({(1200-w*1.5)/2} 225) scale(1.5)">'+lock(*variants[v])+'</g>'
 (root/'svg'/f'cover-{name}.svg').write_text(svg(1200,630,body))
background=Path('/Users/max/.codex/generated_images/01a177bc-4cda-7453-9ec3-567e701f3cee/exec-5fc8ffd8-0d6d-49c2-a13c-1d658164ef40.png')
shutil.copy2(background,root/'backgrounds'/'aura-source.png')
data=base64.b64encode(background.read_bytes()).decode()
for name,cw,ch in [('cover-aura',1200,630),('hero-aura',1920,1080),('social-square',1080,1080)]:
 s=cw*.64/w
 body=f'<image href="data:image/png;base64,{data}" width="{cw}" height="{ch}" preserveAspectRatio="xMidYMid slice"/><g transform="translate({cw*.18} {ch*.62-h*s/2}) scale({s})">'+lock('#ffffff','#ffffff')+'</g>'
 (root/'svg'/f'{name}.svg').write_text(svg(cw,ch,body))
icon=svg(512,512,'<rect width="512" height="512" rx="112" fill="#100d1b"/><g transform="translate(92 66) scale(.37)">'+mark('#ffffff')+'</g>')
(root/'icons'/'favicon.svg').write_text(icon)
(root/'icons'/'site.webmanifest').write_text(json.dumps({'name':'MS Product','short_name':'MS Product','icons':[{'src':'/assets/ms-product/icons/icon-192.png','sizes':'192x192','type':'image/png'},{'src':'/assets/ms-product/icons/icon-512.png','sizes':'512x512','type':'image/png','purpose':'any'}],'theme_color':'#07060b','background_color':'#07060b','display':'standalone'},indent=2))
(root/'source'/'design-notes.txt').write_text('MS Product web kit, 2026-10-26\nSource mark: ../logo-ms-transparent.png (canonical brand folder)\nWordmark: Helvetica Neue Medium, converted to outlines. No runtime font dependency.\nPalette: mark reconstructed from original purple-blue mark; surfaces from site-ms/public/index.html.\nBackground created with built-in image_gen. Prompt: restrained abstract near-black #07060b paper, soft violet #9d6bff and blue light, fine grain, match original reference spatially: asymmetric black mass lower-left, light centered at x51% y5%, dark upper-right, selectively blurred top-left edge and more defined lower-right edge; muted violet-indigo, no bright blue ring, no folds, no crossing planes, no X shapes, no text or symbols. Revised per owner feedback on 2026-10-26.\nAll geometry reconstructed as SVG, not raster embedded in a vector container. Background covers contain an embedded raster background.\n')
print(root, w, 'SVG assets generated')
