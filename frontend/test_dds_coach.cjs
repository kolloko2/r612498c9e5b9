const assert=require('node:assert/strict');
const {nextAction}=require('./assets/dds-coach.js');
const s={exercise_mode:'actions',practice_with_hints:true};
assert.equal(nextAction(s),null);
s.practice_hint={title:'Проверенный шаг',text:'Проверенный текст',target:'responseStatus'};
assert.deepEqual(nextAction(s),s.practice_hint);
assert.equal(nextAction({...s,practice_with_hints:false}),null);
assert.equal(nextAction({...s,exercise_mode:'fill'}),null);
console.log('DDS coach: approved server hints only');
