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
  await page.getByRole('button',{name:'监测总览',exact:true}).click();
  await page.getByRole('button',{name:/SPY MOCK/}).click();
  await page.locator('.price-chart .recharts-line').first().waitFor();
  await page.evaluate(()=>window.scrollTo(0,0));
  await page.screenshot({path:'output/playwright/overview-final.png',fullPage:true});
  
  if(errors.length)throw Error(errors.join('\n'));
  return {manualSave:true,excelResult:result.modes.MID.rawMean,previewDoesNotAddSample:true,persistenceAfterReload:true,pageErrors:errors};
}
