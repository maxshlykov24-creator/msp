// Synthetic presentation data only. No client API or operational data.
export const demoUser={id:'demo',name:'Демонстрация',role:'viewer',permissions:[]};
const items:any[]=[];
const models=[['Лонгслив Essential','Лонгсливы'],['Футболка Classic','Футболки'],['Брюки Relax','Брюки'],['Костюм Everyday','Костюмы']];
models.forEach(([model,category],m)=>['Молочный','Чёрный','Бордовый'].forEach((color,c)=>['S','M','L','XL'].forEach((size,z)=>{
 const id='product-'+m+'-'+c+'-'+z;
 items.push({id,version:1,kind:'garment',model,category,color,size,name:model+' · '+color+' · '+size,unit:'piece',barcodes:['200000'+m+c+z+'001'],photo:'',source:{version:1},balances:['stock','workshop'].map((location,k)=>({location,known:true,free:8+(m*13+c*7+z*3+k)%36,frozen:(c+z)%5,reserved:(m+z)%7,damaged:0}))});
})));
items.push({id:'fabric',version:1,kind:'material',model:'Кулирная гладь',category:'Материалы',color:'Молочный',size:'',name:'Кулирная гладь · Молочный',unit:'meter',barcodes:['200000999001'],photo:'',source:{version:1},balances:[{location:'stock',known:true,free:240,frozen:0,reserved:65,damaged:0}]});
const users=[{id:'cutter-1',name:'Сотрудник 01'},{id:'cutter-2',name:'Сотрудник 02'}];
const tasks=models.map(([model],m)=>{
 const products=items.filter(i=>i.kind==='garment'&&i.model===model&&i.color==='Молочный').map((i,z)=>({item_id:i.id,quantity:12+z*4}));
 const materials=[{item_id:'fabric',quantity:48+m*8}];
 return {id:'task-'+m,number:'КР-00'+(m+1),version:1,title:model+' · Плановая партия',status:m===1?'partial':'approved',created_at:'2026-10-28T10:00:00Z',due_on:'2026-11-05',available_on:'2026-11-08',assignee:users[m%2].id,location:'stock',products,materials,remaining_products:products.map(l=>({...l,quantity:m===1?Math.round(l.quantity/2):l.quantity})),remaining_materials:materials,facts:m===1?['fact-1']:[],reversed_facts:[],cutting:{groups:[{id:'layout-'+m,model,ratio:'S : M : L : XL = 1 : 2 : 2 : 1',material:'fabric',quantity:48+m*8,products:Object.fromEntries(products.map(l=>[l.item_id,l.quantity]))}]}};
});
const freezes=items.slice(0,4).map((i,n)=>({id:'freeze-'+n,version:1,item_id:i.id,remaining:12+n*4,mode:'manual',until:'2026-11-04T10:00:00Z'}));
const orders=items.slice(4,8).map((i,n)=>({id:'order-'+n,version:1,platform:n%2?'ozon':'wb',external_id:'DEMO-'+(101+n),status:n%2?'shipped':'reserved',lines:[{item_id:i.id,quantity:n+1}],shortages:[]}));
export async function demoApi(path:string,_body?:any):Promise<any>{
 const data:any={'catalog':{items},'status':{snapshots:[{location:'stock'}],blockers:[],queue_held:0},'me':demoUser,'entities/task':tasks,'entities/freeze':freezes,'entities/order':orders,'cutting/assignees':users,'journal':tasks.map((t,n)=>({id:'event-'+n,at:t.created_at,kind:'task.create',reason:'Демонстрационная партия',actor:'Сотрудник 01',movements:[]}))};
 if(path in data)return JSON.parse(JSON.stringify(data[path]));
 throw new Error('Эта операция не входит в демонстрационный просмотр');
}
