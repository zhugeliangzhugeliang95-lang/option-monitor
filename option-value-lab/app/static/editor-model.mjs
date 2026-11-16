// Keep contract identity and validation evidence attached to the editable row.
export const fields=['strike','call_last','put_last','call_bid','call_ask','put_bid','put_ask'];
export function toDraft(row,study){
 const original=structuredClone(row),values={strike:row.strike};
 for(const side of ['call','put']){
  if(original[side]){
   original[side].underlying ||= study.symbol;
   original[side].expiration ||= study.expiration;
  }
  for(const key of ['last','bid','ask'])values[side+'_'+key]=row[side]?.[key]??'';
 }
 return {values,original};
}
export function fromDraft(values,original=null){
 if(fields.every(f=>String(values[f]??'').trim()===''))return null;
 const number=value=>String(value??'').trim()===''?null:Number(value);
 if(original&&Number(values.strike)!==Number(original.strike))
  throw Error('已有合约的行权价不能直接更换；请添加新的一行输入新合约。');
 const row=original?structuredClone(original):{};
 row.strike=values.strike;
 for(const side of ['call','put']){
  const previous=original?.[side],q=previous?structuredClone(previous):{};
  for(const key of ['last','bid','ask'])q[key]=number(values[side+'_'+key]);
  if(previous){
   const lastChanged=q.last!==(previous.last??null);
   const bookChanged=q.bid!==(previous.bid??null)||q.ask!==(previous.ask??null);
   if(lastChanged)q.lastAt=null;
   if(bookChanged)q.bookAt=null;
   if(lastChanged||bookChanged)q.updatedAt=null;
  }
  row[side]=previous||['last','bid','ask'].some(k=>q[k]!==null)?q:null;
 }
 return row;
}
