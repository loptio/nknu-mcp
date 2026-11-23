(()=>{const pop=document.querySelector('.help .pop>b');if(pop){const f=document.createElement('div');
  f.innerHTML=`<p class='pd-f'>(<i class='rel'>對題×3</i>＋<i class='add'>學期</i>＋<i class='add'>屆別</i>＋<i class='add'>新舊</i>)×<i class='cut'>還有效</i>×<i class='cut'>對口</i>＋<i class='add'>單位</i></p>`
   +`<p class='pd-fn'>還有效：每過一個學年 ×0.85　對口：系所自己的文件、問題沒點名系所時 ×0.9<br>背景文件（學報、考題、會議紀錄）總分 ×0.8，前 10 名最多 2 份</p>`;pop.replaceWith(...f.childNodes)}
document.querySelectorAll("span[data-tip^='重排模型讀小段']").forEach(x=>{const m=x.textContent.match(/(\d+)/);if(m&&window.KIC)x.innerHTML=`<span class='pd-pc'>重排 ${KIC.svg('chunk',12)}${m[1]}</span>`})})();
