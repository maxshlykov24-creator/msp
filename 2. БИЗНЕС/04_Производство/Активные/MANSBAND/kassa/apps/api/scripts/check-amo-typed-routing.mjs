import assert from 'node:assert/strict';
import {decodeLeadType, typeLabel, typedExecutionKind, migrationBlocked} from '../src/services/amoRouting.ts';

const pipelines = {'Продажа офлайн':11482373,'Продажа онлайн':11482377,'Аренда':11482381,'Продажа компании':11482385};
const lead=(pipeline_id,value)=>({pipeline_id,custom_fields_values:value===undefined?[]:[{field_id:3831526,values:[{value}]}]});
for(const [label,pipeline,kind,funnel] of [
  ['Продажа офлайн',11482373,'sale','offline'],
  ['Продажа онлайн',11482377,'sale','online'],
  ['Аренда',11482381,'rental','offline'],
  ['Продажа компании',11482385,'company','offline'],
  ['Сертификат пластик',11482373,'cert_plastic','offline'],
  ['Сертификат электронный',11482377,'cert_digital','online'],
  ['Возврат',9601246,'refund','return'],
  ['Обмен',9601246,'exchange','return'],
  ['Не слив',9601246,'no_sliv','offline'],
]) assert.deepEqual(decodeLeadType(lead(pipeline,label),pipelines),{kind,funnel});
assert.equal(decodeLeadType(lead(9601214),pipelines),null);
assert.throws(()=>decodeLeadType(lead(11482381),pipelines),/не заполнен/);
assert.throws(()=>decodeLeadType(lead(11482381,'Продажа офлайн'),pipelines),/противоречит/);
assert.throws(()=>decodeLeadType(lead(11482373,'Возврат'),pipelines),/противоречит/);
assert.throws(()=>decodeLeadType(lead(11482373,'Неверный тип'),pipelines),/Неизвестное/);
assert.throws(()=>typeLabel({kind:'deferred',funnel:'offline'}),/базовый вид/);
assert.throws(()=>typeLabel({kind:'promise',funnel:'online'}),/базовый вид/);
assert.throws(()=>typeLabel({kind:'sale',funnel:'defects'}),/Неоднозначный/);
assert.equal(typeLabel({kind:'delivery',funnel:'online'}),'Продажа онлайн');
for (const kind of ['rental','company','cert_digital','cert_plastic','defect']) {
  assert.equal(typedExecutionKind({kind,funnel:'online'},'Собран'),kind);
}
assert.equal(typedExecutionKind({kind:'sale',funnel:'online'},'Собран'),'delivery');
assert.equal(typedExecutionKind({kind:'sale',funnel:'offline'},'Собран'),'sale');
assert.equal(typedExecutionKind({kind:'sale',funnel:'online'},'Успех','delivery'),'delivery');
assert.equal(migrationBlocked({_embedded:{tags:[{name:'MB_MIGRATION_IN_PROGRESS'}]}}),true);
assert.equal(migrationBlocked({_embedded:{tags:[{name:'MB_MIGRATION_DONE'}]}}),false);
console.log('28 typed routing checks passed');
