"""Build original client UIs against isolated synthetic data. Never edits client sources."""
from pathlib import Path
import re, shutil, subprocess, tempfile, json

site=Path(__file__).resolve().parents[1]
vault=next(p for p in site.parents if (p/'.cursor/rules/00-ядро.mdc').exists())
clients=vault/'2. БИЗНЕС/04_Производство/Активные'
out=site/'public/brands/previews'
out.mkdir(exist_ok=True)
adapters=site/'demo-adapters'
guard=(adapters/'guard.js').read_text()
csp="default-src 'none'; script-src 'self' 'unsafe-inline'; style-src 'self' 'unsafe-inline'; img-src 'self' data: blob:; font-src 'self' data:; connect-src 'none'; form-action 'none'; base-uri 'none'"
def prepare(html,extra=''):
    # All executable code remains original, except the data API and origin isolation.
    if '<head>' not in html:
        html=re.sub(r'(<html[^>]*>)',r'\1<head>',html,count=1)
    return html.replace('<head>','<head><meta http-equiv="Content-Security-Policy" content="'+csp+'"><script>'+guard+extra+'</script>',1)

web=clients/'MANSBAND/kassa/apps/web'
stage=Path(tempfile.mkdtemp(prefix='msp-client-preview-'))
for name in ['src','public']:
    shutil.copytree(web/name,stage/name)
for name in ['index.html','tailwind.config.js','postcss.config.js','package.json']:
    shutil.copy2(web/name,stage/name)
(stage/'node_modules').symlink_to(web/'node_modules',target_is_directory=True)
(stage/'src/api/client.ts').write_text((adapters/'mansband-api.ts').read_text())
p=stage/'src/main.tsx';p.write_text(p.read_text().replace('await clearStaleClient();','// Preview must not touch origin service workers or caches.'))
p=stage/'src/views/ProductCheck.tsx';s=p.read_text();s=s.replace('if (USE_MOCK)', 'if (false)');p.write_text(s)
# Replace fixture identities too; no customer information is copied to the public bundle.
p=stage/'src/data/mock.ts';s=p.read_text();s=re.sub(r'\+7[\d ()-]{9,}', '+7 000 000-00-00',s)
for key in ['clientName','client','guestName','recipientName','consultant','callManager','email','address']:
    counter=[0]
    def identity(m):
        counter[0]+=1
        label='Консультант' if key in ['consultant','callManager'] else 'Покупатель'
        return m[1]+json.dumps('demo@example.invalid' if key=='email' else label+' '+str(counter[0]).zfill(2),ensure_ascii=False)
    s=re.sub(r'('+key+r':\s*)"[^"]*"',identity,s)
s=re.sub(r'2026-0[3-9]-\d\d','2026-10-20',s)
p.write_text(s)
shared=clients/'MANSBAND/kassa/packages/shared/src/index.ts'
(stage/'vite.config.ts').write_text('import {defineConfig} from "vite";import react from "@vitejs/plugin-react";export default defineConfig({plugins:[react()],base:"./",resolve:{alias:{"@kassa/shared":'+json.dumps(str(shared))+'}},build:{outDir:'+json.dumps(str(out/'mansband'))+',emptyOutDir:false,cssCodeSplit:false,modulePreload:false,rollupOptions:{output:{format:"iife",inlineDynamicImports:true}}}})')
subprocess.run([str(vault.parent/'.local/bin/node'),str(web/'node_modules/vite/bin/vite.js'),'build'],cwd=stage,check=True,env={**__import__('os').environ,'VITE_USE_MOCK':'1'})
p=out/'mansband/index.html';s=p.read_text();s=re.sub(r'type="module"\s+crossorigin','defer',s);s=s.replace('crossorigin','');p.write_text(prepare(s))
for name,source in [('analytics',clients/'2MyMoods/дашборд/2MY_дашборд_прототип.html'),('production',clients/'2MyMoods/_код/production.html')]:
    target=out/name;target.mkdir(exist_ok=True)
    html=source.read_text();extra=(adapters/(name+'.js')).read_text()
    html=html.replace('href="/production"','href="../production/index.html"').replace('href="/"','href="../analytics/index.html"')
    if name=='production':
        html,n=re.subn(r"async function api\(path='',body\)\{[^\n]+\}","async function api(path='',body){return demoProduction(path,body)}",html,count=1)
        assert n==1,'Production API boundary changed; inspect source before rebuilding'
    (target/'index.html').write_text(prepare(html,extra))
print('Built original client previews with synthetic data:',out)
