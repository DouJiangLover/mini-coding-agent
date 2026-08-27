import type { Metadata } from 'next';
import './globals.css';

export const metadata: Metadata = {
  title: 'TraceCoder · 可视化编程智能体',
  description: '一个支持 Skill、受控本地工具和实时执行轨迹的轻量级 Coding Agent。',
};

export default function RootLayout({ children }: Readonly<{ children: React.ReactNode }>) {
  return (
    <html lang="zh-CN">
      <body>{children}</body>
    </html>
  );
}
