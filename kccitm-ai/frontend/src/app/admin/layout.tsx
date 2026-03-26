'use client';
import { useEffect } from 'react';
import { useRouter, usePathname } from 'next/navigation';
import { isAuthenticated, isAdmin } from '@/lib/auth';
import Link from 'next/link';
import {
  LayoutDashboard, MessageSquareWarning, Wrench, FileText,
  HelpCircle, Blocks, Database, Cpu, Activity, ArrowLeft
} from 'lucide-react';

const NAV = [
  { href: '/admin', label: 'Overview', icon: LayoutDashboard },
  { href: '/admin/feedback', label: 'Feedback', icon: MessageSquareWarning },
  { href: '/admin/healing', label: 'Healing', icon: Wrench },
  { href: '/admin/prompts', label: 'Prompts', icon: FileText },
  { href: '/admin/faqs', label: 'FAQs', icon: HelpCircle },
  { href: '/admin/chunks', label: 'Chunks', icon: Blocks },
  { href: '/admin/training', label: 'Training', icon: Database },
  { href: '/admin/models', label: 'Models', icon: Cpu },
  { href: '/admin/system', label: 'System', icon: Activity },
];

export default function AdminLayout({ children }: { children: React.ReactNode }) {
  const router = useRouter();
  const pathname = usePathname();

  useEffect(() => {
    if (!isAuthenticated()) router.push('/');
    else if (!isAdmin()) router.push('/chat');
  }, [router]);

  return (
    <div className="h-screen flex">
      <div className="w-48 bg-white border-r border-gray-200 flex flex-col flex-shrink-0">
        <div className="p-3 border-b border-gray-200 flex items-center gap-2">
          <div className="w-8 h-8 rounded-lg bg-kcc flex items-center justify-center">
            <span className="text-white text-sm font-bold">K</span>
          </div>
          <span className="text-[11px] font-bold text-kcc">KCCITM Admin</span>
        </div>
        <nav className="flex-1 p-1.5 overflow-y-auto">
          {NAV.map(item => {
            const active = pathname === item.href;
            return (
              <Link key={item.href} href={item.href}
                className={`flex items-center gap-2 px-3 py-2 rounded-lg text-xs mb-0.5 transition-all ${active ? 'bg-kcc-bg text-kcc-text font-semibold' : 'text-gray-500 hover:bg-gray-50 hover:text-gray-700'}`}>
                <item.icon size={14} /> {item.label}
              </Link>
            );
          })}
        </nav>
        <Link href="/chat" className="p-3 border-t border-gray-200 text-[11px] text-kcc font-medium flex items-center gap-1 hover:bg-gray-50">
          <ArrowLeft size={12} /> Back to chat
        </Link>
      </div>
      <div className="flex-1 overflow-y-auto p-6 min-w-0 bg-gray-50">{children}</div>
    </div>
  );
}
