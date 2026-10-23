async page => {
  const base=new URL(page.url()).origin;
  const errors=[];page.on('pageerror', e=>errors.push(String(e)));
  await page.getByRole('button',{name:/EXCEL MANUAL/}).click();
  await page.getByRole('button',{name:'人工报价',exact:true}).click();
  await page.getByRole('button',{name:'计算并保存快照'}).waitFor();
  const monitors=await (await page.request.get(base+'/api/monitors')).json();
  const monitor=monitors.find(m=>m.config.symbol==='EXCEL');
  const before=await (await page.request.get(`${base}/api/monitors/${monitor.id}/history`)).json();
  await page.getByRole('button',{name:'计算并保存快照'}).click();
  await page.getByRole('status').filter({hasText:'快照已保存'}).waitFor();
  const after=await (await page.request.get(`${base}/api/monitors/${monitor.id}/history`)).json();
  if(after.length!==before.length+1)throw Error('Manual save did not append exactly one snapshot');
  const result=after.at(-1);
  if(Math.abs(result.modes.MID.rawMean-157.19822490096)>1e-9)throw Error('Excel result mismatch');
  await page.getByRole('button',{name:'卖一',exact:true}).click();
  await page.getByText('历史重算预览',{exact:false}).waitFor();
  const afterPreview=await (await page.request.get(`${base}/api/monitors/${monitor.id}/history`)).json();
  if(afterPreview.length!==after.length)throw Error('Preview generated a sample');
  await page.reload();
  await page.getByRole('button',{name:/EXCEL MANUAL/}).click();
  await page.getByRole('button',{name:'锁定当前研究观察'}).click();
  await page.getByRole('status').filter({hasText:'已锁定'}).waitFor();
  await page.getByRole('button',{name:'历史与追溯',exact:true}).click();
  await page.getByRole('heading',{name:'已锁定研究观察'}).waitFor();
  const frozen=await (await page.request.get(`${base}/api/research/frozen-observations?monitorId=${monitor.id}`)).json();
  if(!frozen.length)throw Error('Frozen observation not persisted');
  const csv=await page.request.get(`${base}/api/exports/${monitor.id}?format=csv`);
  if(!csv.ok()||!(await csv.text()).includes('configHash'))throw Error('CSV failed');
  await page.getByRole('button',{name:'监测总览',exact:true}).click();
  await page.getByRole('button',{name:/SPY MOCK/}).click();
  await page.locator('.price-chart .recharts-line').first().waitFor();
  await page.evaluate(()=>window.scrollTo(0,0));
  await page.screenshot({path:'output/playwright/overview-final.png',fullPage:true});
  
  if(errors.length)throw Error(errors.join('\n'));
  return {manualSave:true,excelResult:result.modes.MID.rawMean,previewDoesNotAddSample:true,persistenceAfterReload:true,frozenCount:frozen.length,csvExport:true,pageErrors:errors};
}
