const fs=require('fs'),path=require('path'),sharp=require('sharp');const root=path.resolve(__dirname,'..');
(async()=>{
fs.mkdirSync(path.join(root,'jpg'),{recursive:true});
for(const n of ['cover-aura','cover-dark','cover-light','cover-violet','hero-aura','social-square']) await sharp(path.join(root,'png',n+'.png')).jpeg({quality:94,mozjpeg:true}).toFile(path.join(root,'jpg',n+'.jpg'));
let items=[];for(const [i,size] of [16,32,48].entries())items.push({input:await sharp(path.join(root,'icons',`favicon-${size}.png`)).resize(size*5,size*5,{kernel:'nearest'}).toBuffer(),left:i*260+30,top:30});
await sharp({create:{width:800,height:300,channels:4,background:'#dcdce4'}}).composite(items).png().toFile(path.join(root,'source','favicon-check.png'));
let report=[];
for(const folder of ['png','webp','jpg','icons'])for(const f of fs.readdirSync(path.join(root,folder))){if(!/\.(png|webp|jpg)$/.test(f))continue;const p=path.join(root,folder,f),m=await sharp(p).metadata();if(!m.width||!m.height)throw Error(f); if(folder==='png'&&(f.startsWith('logo-')||f.startsWith('mark-'))){const s=await sharp(p).stats();if(!m.hasAlpha||s.channels[3].min!==0)throw Error('Missing transparency '+f)}report.push(`${folder}/${f}: ${m.width}x${m.height}, alpha=${m.hasAlpha}`)}
fs.writeFileSync(path.join(root,'source','validation.txt'),report.join('\n'));console.log(report.length+' raster files decoded; transparent logos verified');
})();
