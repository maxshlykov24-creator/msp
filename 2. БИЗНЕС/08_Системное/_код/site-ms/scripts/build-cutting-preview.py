"""Build an anonymous, isolated preview from the original cutting frontend."""
from pathlib import Path
import tempfile,shutil,re,json,subprocess,os
site=Path(__file__).resolve().parents[1]
web=site.parent/'kroy-sync/web'
stage=Path(tempfile.mkdtemp(prefix='msp-cutting-preview-'))
shutil.copytree(web/'src',stage/'src')
shutil.copy2(web/'index.html',stage/'index.html')
(stage/'node_modules').symlink_to(web/'node_modules',target_is_directory=True)
shutil.copy2(site/'demo-adapters/cutting.ts',stage/'src/demo.ts')
p=stage/'src/Catalog.tsx';p.write_text(p.read_text().replace("?'◉':'Y'","?'◉':'◇'"))
p=stage/'src/main.tsx';s=p.read_text()
s=s.replace("import React,{useState}","import React,{useState,useEffect}")
s="import {demoApi,demoUser} from './demo';\n"+s
s=re.sub(r'async function api\(path:string,body\?:any,auth=token\)\{[^\n]+\}',"async function api(path:string,body?:any,auth=token){return demoApi(path,body)}",s,count=1)
s=s.replace('[me,setMe]=useState<any>(null)','[me,setMe]=useState<any>(demoUser)')
s=s.replace("const primary=['catalog','tasks','freeze','orders','count',...(can('finance')?['finance']:[])];","const primary=['catalog','tasks','freeze','orders'];")
s=s.replace("const utilities=['journal','attention',...(can('settings')?['staff']:[]),'mappings']","const utilities=['journal']")
s=s.replace('function logout(){', 'function logout(){return;')
s=s.replace('const can=(p:string)',"useEffect(()=>{load().catch(e=>setError(String(e)))},[]);\nconst can=(p:string)")
s=s.replace('YUJI.CLO','Бренд 4').replace('Проверочная среда · публикация на площадки выключена','Демонстрационный интерфейс · данные вымышлены')
p.write_text(s)
out=site/'public/brands/previews/cutting'
(stage/'vite.config.mjs').write_text('export default '+json.dumps({'base':'./','build':{'outDir':str(out.resolve()),'emptyOutDir':False,'cssCodeSplit':False,'modulePreload':False,'rollupOptions':{'output':{'format':'iife','inlineDynamicImports':True}}}}))
subprocess.run([str(Path.home()/'.local/bin/node'),str(web/'node_modules/vite/bin/vite.js'),'build'],cwd=stage,check=True)
p=out/'index.html';s=p.read_text().replace('type="module" crossorigin','defer').replace('crossorigin','').replace('YUJI.CLO','Бренд 4')
guard=(site/'demo-adapters/guard.js').read_text()
csp="default-src 'none'; script-src 'self' 'unsafe-inline'; style-src 'self' 'unsafe-inline'; img-src 'self' data: blob:; font-src 'self' data:; connect-src 'none'; form-action 'none'; base-uri 'none'"
s=s.replace('<head>','<head><meta http-equiv="Content-Security-Policy" content="'+csp+'"><script>'+guard+'</script>')
p.write_text(s)
print('Built anonymous cutting preview:',out)
