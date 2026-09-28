import { lazy, Suspense, type ReactNode } from 'react'
import { createBrowserRouter, createHashRouter } from 'react-router-dom'
import { Shell } from '@/components/shell'
import { CardSkeleton } from '@/components/states'
import { NotFoundPage } from '@/pages/not-found'

const TonightPage = lazy(() => import('@/pages/tonight').then((m) => ({ default: m.TonightPage })))
const TomorrowPage = lazy(() => import('@/pages/tomorrow').then((m) => ({ default: m.TomorrowPage })))
const MemberPage = lazy(() => import('@/pages/member').then((m) => ({ default: m.MemberPage })))
const MarketPage = lazy(() => import('@/pages/market').then((m) => ({ default: m.MarketPage })))
const HowItWorksPage = lazy(() => import('@/pages/how-it-works').then((m) => ({ default: m.HowItWorksPage })))

const page = (node: ReactNode) => <Suspense fallback={<CardSkeleton lines={6} />}>{node}</Suspense>

// Static hosts have no SPA fallback, so a hosted build uses hash routes (VITE_HASH_ROUTER=1).
const createRouter = import.meta.env.VITE_HASH_ROUTER === '1' ? createHashRouter : createBrowserRouter

export const router = createRouter([
  {
    path: '/',
    element: <Shell />,
    children: [
      { index: true, element: page(<TonightPage />) },
      { path: 'tomorrow', element: page(<TomorrowPage />) },
      { path: 'member', element: page(<MemberPage />) },
      { path: 'member/:deviceId', element: page(<MemberPage />) },
      { path: 'market', element: page(<MarketPage />) },
      { path: 'how-it-works', element: page(<HowItWorksPage />) },
      { path: '*', element: <NotFoundPage /> },
    ],
  },
])
