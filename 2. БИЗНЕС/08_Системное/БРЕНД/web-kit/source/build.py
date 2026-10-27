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
# Approved master: one source for all current exports.
import re
master=(root/'source/master-mark.svg').read_text()
paths=re.findall(r'<path d="([^"]+)"',re.search(r'<clipPath.*?</clipPath>',master).group())
masterbody=master[master.index('>')+1:master.rindex('</svg>')]
darkmaster=(root/'source/master-mark-dark.svg').read_text()
darkbody=darkmaster[darkmaster.index('>')+1:darkmaster.rindex('</svg>')]
def mark(fill):
 if fill=='dark-texture':return darkbody
 if fill=='url(#brand)':return masterbody
 return '<g fill="'+fill+'">'+''.join('<path d="'+p+'"/>' for p in paths)+'</g>'
def word(fill): return '<g fill="'+fill+'" transform="scale('+str(scale)+' '+str(-scale)+')">'+''.join(parts)+'</g>'
def defs(): return '<defs><linearGradient id="brand" x1="0" y1="0" x2="1" y2=".6"><stop stop-color="#b56e87"/><stop offset=".55" stop-color="#99516c"/><stop offset="1" stop-color="#6b2b46"/></linearGradient></defs>'
def svg(w,h,body):return f'<svg xmlns="http://www.w3.org/2000/svg" width="{w}" height="{h}" viewBox="0 0 {w} {h}" role="img" aria-label="MS Product">'+defs()+body+'</svg>'
def lock(markfill,textfill):return '<g transform="translate(16 8) scale(.12)">'+mark('dark-texture' if markfill=='url(#brand)' and textfill in ('#ffffff','#f5edf0') else markfill)+'</g><g transform="translate(134 85)">'+word(textfill)+'</g>'
w=round(150+textwidth);h=120
variants={'gradient-dark':('url(#brand)','#241219'),'gradient-light':('url(#brand)','#f5edf0'),'white':('#ffffff','#ffffff'),'black':('#241219','#241219'),'violet':('#71304a','#71304a'),'full-gradient':('url(#brand)','url(#brand)')}
for name,(m,t) in variants.items():
 (root/'svg'/f'logo-horizontal-{name}.svg').write_text(svg(w,h,lock(m,t)))
 (root/'svg'/f'mark-{name}.svg').write_text(svg(1040,1160,'<g transform="translate(78 68)">'+mark('dark-texture' if name=='gradient-light' else m)+'</g>'))
for name,bg,v in [('dark','#130b10','gradient-light'),('light','#faf9f6','gradient-dark'),('violet','#71304a','white')]:
 body=f'<rect width="1200" height="630" fill="{bg}"/><g transform="translate({(1200-w*1.5)/2} 225) scale(1.5)">'+lock(*variants[v])+'</g>'
 (root/'svg'/f'cover-{name}.svg').write_text(svg(1200,630,body))
background=root/'backgrounds'/'aura-source.png'
data=base64.b64encode(background.read_bytes()).decode()
for name,cw,ch in [('cover-aura',1200,630),('hero-aura',1920,1080),('social-square',1080,1080)]:
 s=cw*.64/w
 body=f'<image href="data:image/png;base64,{data}" width="{cw}" height="{ch}" preserveAspectRatio="xMidYMid slice"/><g transform="translate({cw*.18} {ch*.62-h*s/2}) scale({s})">'+lock('url(#brand)','#ffffff')+'</g>'
 (root/'svg'/f'{name}.svg').write_text(svg(cw,ch,body))
icon=svg(512,512,'<rect width="512" height="512" rx="112" fill="#29101d"/><g transform="translate(100 76) scale(.39)">'+mark('#ffffff')+'</g>')
(root/'icons'/'favicon.svg').write_text(icon)
(root/'icons'/'site.webmanifest').write_text(json.dumps({'name':'MS Product','short_name':'MS Product','icons':[{'src':'/assets/ms-product/icons/icon-192.png','sizes':'192x192','type':'image/png'},{'src':'/assets/ms-product/icons/icon-512.png','sizes':'512x512','type':'image/png','purpose':'any'}],'theme_color':'#130b10','background_color':'#130b10','display':'standalone'},indent=2))
print('Master applied to all SVG exports')
