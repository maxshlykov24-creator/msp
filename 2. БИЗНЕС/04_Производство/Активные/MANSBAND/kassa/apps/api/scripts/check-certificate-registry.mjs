import assert from 'node:assert/strict';
const url=process.env.CERT_TEST_DATABASE_URL;
if(!url || !url.includes('certificate_registry_test'))throw new Error('Use the isolated certificate_registry_test database only');
Object.assign(process.env,{DATABASE_URL:url,JWT_SECRET:'test-only-secret-at-least-16',AMOCRM_API_BASE:'https://example.test/api/v4',AMOCRM_LONG_LIVED_TOKEN:'test-only-token-not-a-secret',AMOCRM_PIPELINE_SALES:'9601214',AMOCRM_PIPELINE_COMPLAINTS:'9601246',MOYSKLAD_API_BASE:'https://example.test',MOYSKLAD_API_TOKEN:'test-only-token-not-a-secret'});
const {db,sql:pg}=await import('../dist/db/index.js');const {sql}=await import('drizzle-orm');
const r=await import('../dist/services/certificateRegistry.js');const c=await import('../dist/services/certificates.js');
const {moscowTodayYmd}=await import('@kassa/shared');
try{
await db.execute(sql`CREATE TABLE IF NOT EXISTS certificates (number text PRIMARY KEY,nominal integer NOT NULL,balance integer NOT NULL,status text NOT NULL,type text NOT NULL,guest_name text,buyer_deal_number integer,issued_at text NOT NULL,valid_until text NOT NULL)`);
await r.initializeCertificateRegistry();await r.initializeCertificateRegistry();
const reserved=await r.reservedNumbers();assert(reserved.some(x=>x.number==='00873'));assert.equal(r.canonicalNumber(' mb48379261 '),'MB-4837-9261');assert.equal(r.canonicalNumber('00881'),'00881');
const base={number:'',nominal:100,balance:100,status:'active',type:'digital',issuedAt:moscowTodayYmd(),validFrom:moscowTodayYmd(),validUntil:'2099-01-01'};
const heldSource='test:'+crypto.randomUUID();const heldNumber=await r.reserveNumber(heldSource);assert(!(await c.list()).some(x=>x.number===heldNumber));const completed=await r.ensureCertificate(heldSource,base);assert.equal(completed.number,heldNumber);
const source='test:'+crypto.randomUUID();const issued=await Promise.all(Array.from({length:8},()=>r.ensureCertificate(source,base)));
assert.equal(new Set(issued.map(x=>x.number)).size,1);assert.match(issued[0].number,/^MB-\d{4}-\d{4}$/);
await assert.rejects(()=>r.ensureCertificate('test:'+crypto.randomUUID(),base,'00873'));
const spent=await Promise.all(Array.from({length:8},()=>c.redeem(issued[0].number,30)));assert.equal(spent.filter(Boolean).length,3);
const same=await r.ensureCertificate(source,base);assert.equal(same.balance,10);assert.equal(same.number,issued[0].number);
const future=await r.ensureCertificate('test:'+crypto.randomUUID(),{...base,validFrom:'2098-01-01'});assert.equal(await c.redeem(future.number,1),null);
await assert.rejects(()=>c.redeemMany([{number:future.number,amountRub:1}]));
const a=await r.ensureCertificate('test:'+crypto.randomUUID(),base);const b=await r.ensureCertificate('test:'+crypto.randomUUID(),base);
await assert.rejects(()=>c.redeemMany([{number:a.number,amountRub:10},{number:b.number,amountRub:101}]));
const after=(await c.list()).find(x=>x.number===a.number);assert.equal(after.balance,100);
console.log(JSON.stringify({checks:['legacy reservations and zeros','canonical format','concurrent issuance','reserved conflict','atomic spending','repeat issuance keeps balance','future date blocks both spending paths','batch rollback'],result:'passed'}));
}finally{await pg.end();}
