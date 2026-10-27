const fs=require('fs'),path=require('path'),sharp=require('sharp');
const root=path.resolve(__dirname,'..'),brand=path.dirname(root),tests=path.join(brand,'logo-tests');
(async()=>{
const report=[];
for(const file of fs.readdirSync(path.join(root,'svg')).filter(f=>f.endsWith('.svg'))){
 const n=file.slice(0,-4),src=path.join(root,'svg',file),isCover=/^(cover-|hero-|social-)/.test(n);
 const width=isCover?(n==='social-square'?2160:3840):n.startsWith('mark-')?1216:n.startsWith('logo-horizontal')?2400:2440;
 await sharp(src).resize({width}).png().toFile(path.join(root,'png',n+'.png'));
 await sharp(src).resize({width:isCover?1920:Math.min(width,1600)}).webp({quality:95}).toFile(path.join(root,'webp',n+'.webp'));
 if(isCover)await sharp(src).resize({width}).jpeg({quality:96,chromaSubsampling:'4:4:4'}).toFile(path.join(root,'jpg',n+'.jpg'));
 const m=await sharp(path.join(root,'png',n+'.png')).metadata();report.push(`${n}: PNG ${m.width}x${m.height}`);
}
for(const n of ['aura','aura-light'])await sharp(path.join(root,'backgrounds',n+'-source.png')).webp({quality:96}).toFile(path.join(root,'backgrounds',n+'.webp'));
for(const size of [16,32,48,180,192,512])await sharp(path.join(root,'icons/favicon.svg')).resize(size,size).png().toFile(path.join(root,'icons',size===180?'apple-touch-icon.png':size<100?`favicon-${size}.png`:`icon-${size}.png`));
const sizes=[16,32,48],bufs=sizes.map(s=>fs.readFileSync(path.join(root,'icons',`favicon-${s}.png`)));const h=Buffer.alloc(54);h.writeUInt16LE(1,2);h.writeUInt16LE(3,4);let offset=h.length;for(let i=0;i<3;i++){let o=6+i*16;h[o]=h[o+1]=sizes[i];h.writeUInt16LE(1,o+4);h.writeUInt16LE(32,o+6);h.writeUInt32LE(bufs[i].length,o+8);h.writeUInt32LE(offset,o+12);offset+=bufs[i].length;}fs.writeFileSync(path.join(root,'icons/favicon.ico'),Buffer.concat([h,...bufs]));
const master=path.join(root,'source/master-mark.svg');
await sharp(master).png().toFile(path.join(brand,'logo-ms-transparent.png'));
await sharp(master).flatten({background:'#130b10'}).png().toFile(path.join(brand,'logo-ms.png'));
fs.copyFileSync(path.join(brand,'logo-ms-transparent.png'),path.join(tests,'logo-ms-burgundy-refined.png'));
for(const mode of ['light','dark','transparent'])for(const suffix of ['','-refined'])await sharp(path.join(tests,`vertical-burgundy-${mode}${suffix}.svg`)).png().toFile(path.join(tests,`vertical-burgundy-${mode}${suffix}.png`));
for(const n of ['cover-aura-burgundy.png','cover-aura-burgundy-calm.png'])fs.copyFileSync(path.join(root,'png/cover-aura.png'),path.join(tests,n));
fs.copyFileSync(path.join(root,'png/cover-aura.png'),path.join(root,'png/cover-aura-approved.png'));
const cells=['cover-aura','cover-aura-light','cover-dark','cover-light'];const parts=[];for(let i=0;i<4;i++)parts.push({input:await sharp(path.join(root,'png',cells[i]+'.png')).resize(600,315).toBuffer(),left:(i%2)*600,top:Math.floor(i/2)*315});
await sharp({create:{width:1200,height:630,channels:4,background:'#130b10'}}).composite(parts).png().toFile(path.join(root,'preview.png'));
const alpha=await sharp(path.join(brand,'logo-ms-transparent.png')).stats();if(alpha.channels[3].min!==0||alpha.channels[3].max!==255)throw Error('Invalid transparency');
fs.writeFileSync(path.join(root,'source/validation.txt'),report.join('\n')+'\nTransparent master alpha verified.\nTexture native:1216x1293. Background native:1730x909. 3840px covers combine vector logo/text with enlarged atmospheric raster background.');console.log(report.length+' SVG assets exported; alpha verified');
})();
