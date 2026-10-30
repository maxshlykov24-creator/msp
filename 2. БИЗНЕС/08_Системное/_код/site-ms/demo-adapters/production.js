// Fixture API only. No source client records or network operations.
const demoProduction=(()=>{
 const assortment=[],recipes=[],names=['Кардиган объёмный','Жакет укороченный','Брюки прямые','Юбка миди','Джемпер фактурный','Топ базовый'];
 names.forEach((name,i)=>['XS-S','M-L','XL'].forEach((size,j)=>{
  const id='demo-item-'+i+'-'+j,color=i%2?'Чёрный':'Молочный';
  assortment.push({id,name:name+' ('+color+', '+size+')',model:name,pathName:['Кардиганы','Жакеты','Брюки','Юбки','Джемперы','Топы'][i],code:'DEMO-'+i+j,composition:'Демонстрационный состав',meta:{type:'variant',uuidHref:''},characteristics:[{name:'Цвет',value:color},{name:'Размер',value:size}],uom:{name:'шт'}});
  if(i<5)recipes.push({id:'demo-recipe-'+i+j,name,model:name,color,size,assortment_id:id,assortment_name:name,assortment_url:'',approved:i<3,version:1,editor_schema:2,zip_checked:true,stages:[{name:'Пошив',factory:'demo-factory',work_cost:1400+i*100,materials:[{id:'demo-material',name:'Основная ткань',type:'product',unit:'м',input_quantity:1.6+j*.2,input_unit:'м',quantity:1.6+j*.2},{id:'demo-package',name:'Зип-пакет',type:'product',unit:'шт',input_quantity:1,input_unit:'шт',quantity:1,role:'packaging'}]}]});
 }));
 assortment.push({id:'demo-material',name:'Основная ткань',pathName:'Материалы',meta:{type:'product',uuidHref:''},uom:{name:'м'},buyPrice:{value:55000}},{id:'demo-package',name:'Зип-пакет',pathName:'Упаковка',meta:{type:'product',uuidHref:''},uom:{name:'шт'},buyPrice:{value:2500}});
 const store=[{id:'demo-store',name:'Основной склад'},{id:'demo-workshop',name:'Производство'}];
 const state={config:{company_store:'demo-store',start_at:'2026-10-01',factories:[{id:'demo-factory',name:'Швейное производство',agent:'demo-agent',store:'demo-workshop',store_ok:true,store_name:'Производство'}]},recipes,jobs:[],chains:[]};
 return async function(path='',body){
  if(path.startsWith('/catalogues'))return {ready:true,updated:1793354400,refreshing:false,catalogues:{assortment,store,counterparty:[{id:'demo-agent',name:'Швейное производство',tags:['Производство']}]}};
  if(path==='/setup')return {ready:true,hook_paths:[]};
  if(path==='/recipes/apply'){for(const r of body.recipes){const index=state.recipes.findIndex(x=>x.id===r.id);r.version=(r.version||0)+1;if(index<0)state.recipes.push(r);else state.recipes[index]=r}return {ok:true,recipes:body.recipes};}
  if(path==='/config'){state.config=body;return {ok:true};}
  if(path.startsWith('/analytics'))return {updated:Math.floor(Date.now()/1000),rows:assortment.map((x,i)=>({id:x.id,stock:4+i%13,reserve:i%3,sale:(8900+i%6*900)*100})),production_costs:Object.fromEntries(assortment.map(x=>{const r=state.recipes.find(r=>r.assortment_id===x.id);return [x.id,{cost:r?r.stages.reduce((sum,s)=>sum+Number(s.work_cost||0)+s.materials.reduce((sum,m)=>sum+Number(m.input_quantity||m.quantity)*(m.role==='packaging'?25:550),0),0):null,issue:r?'':'Норма изделия не заполнена'}]}))};
  if(path.startsWith('/stock'))return {rows:assortment.map(x=>({...x,stock:25,reserve:2,unit:x.uom?.name,price_kopecks:55000}))};
  if(path==='/preview')return {stages:[],materials:[],total:0};
  if(!path)return structuredClone(state);
  return {ok:true};
 };
})();
