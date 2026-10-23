import type { Metadata } from 'next';
import './globals.css';
export const metadata: Metadata={title:'Parity Lab · 期权反推价格监测台',description:'可追溯的期权平价研究工作台'};
export default function Layout({children}:{children:React.ReactNode}){return <html lang="zh-CN"><body>{children}</body></html>}
