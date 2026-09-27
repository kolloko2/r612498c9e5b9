const {test}=require('node:test');
const assert=require('node:assert/strict');
const fs=require('node:fs');
const vm=require('node:vm');
const source=fs.readFileSync(require.resolve('./assets/generation.js'),'utf8');
const helpers=source.slice(source.indexOf('function readableValue'),source.indexOf('function renderScenario'));
const context=vm.createContext({fieldNames:{house:'Дом'}});
vm.runInContext(helpers,context);
test('preview renders nested service phones and booleans as text',()=>{
 assert.equal(context.readableValue({'Служба 101':'205'}),'Служба 101: 205');
 assert.equal(context.readableValue(false),'Нет');
 assert.equal(context.readableValue({house:'17'}),'Дом: 17');
 assert.equal(context.readableValue([]),'Не задано');
});
test('decision expectation has readable labels without raw JSON',()=>{
 assert.deepEqual(Array.from(context.decisionLines({should_accept:true,expected_corrections:{house:'17'}})),['Карточку следует принять: Да','Исправления в карточке: Дом: 17']);
});
