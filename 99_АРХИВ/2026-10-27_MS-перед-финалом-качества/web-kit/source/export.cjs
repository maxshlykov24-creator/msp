const fs=require('fs'),path=require('path'),sharp=require('sharp');
const root=path.resolve(__dirname,'..');
(async()=>{
for(const file of fs.readdirSync(path.join(root,'svg'))){
 const name=file.slice(0,-4), src=path.join(root,'svg',file);
 let width=name.startsWith('mark-')?512:name.startsWith('logo-')?1074:undefined;
 await sharp(src).resize({width}).png().toFile(path.join(root,'png',name+'.png'));
 await sharp(src).resize({width}).webp({lossless:true}).toFile(path.join(root,'webp',name+'.webp'));
}
for(const size of [16,32,48,180,192,512]) await sharp(path.join(root,'icons','favicon.svg')).resize(size,size).png().toFile(path.join(root,'icons',size===180?'apple-touch-icon.png':size<100?`favicon-${size}.png`:`icon-${size}.png`));
await sharp(path.join(root,'backgrounds','aura-source.png')).webp({quality:90}).toFile(path.join(root,'backgrounds','aura.webp'));
// Contact sheet: each logo shown on its intended surface.
const cells=[['cover-aura','#07060b'],['cover-light','#f8f7fc'],['cover-dark','#07060b'],['cover-violet','#7546df']];
const composites=[];
for(let i=0;i<cells.length;i++)composites.push({input:await sharp(path.join(root,'png',cells[i][0]+'.png')).resize(600,315).toBuffer(),left:(i%2)*600,top:Math.floor(i/2)*315});
await sharp({create:{width:1200,height:630,channels:4,background:'#07060b'}}).composite(composites).png().toFile(path.join(root,'preview.png'));
console.log('Exported PNG, WebP, icon sizes and preview');
})();
