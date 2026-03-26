'use client';
import { useEffect } from 'react';
import { useRouter } from 'next/navigation';
import LoginForm from '@/components/LoginForm';
import { isAuthenticated } from '@/lib/auth';

export default function Home() {
  const router = useRouter();
  useEffect(() => { if (isAuthenticated()) router.push('/chat'); }, [router]);

  return (
    <div className="min-h-screen flex items-center justify-center bg-gradient-to-br from-gray-50 via-gray-100 to-kcc-bg">
      <LoginForm />
    </div>
  );
}
