import { createBrowserRouter, RouterProvider, Navigate } from 'react-router-dom'
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { Layout } from './components/Layout'
import { ThemeProvider } from './components/ThemeProvider'
import { HomePage } from './features/home/HomePage'
import { SearchResultsPage } from './features/search/SearchResultsPage'
import { AdminSettingsPage } from './features/admin/AdminSettingsPage'
import { AdminAnnouncementPage } from './features/admin/AdminAnnouncementPage'
import { AdminDataPage } from './features/admin/AdminDataPage'
import { AdminFilePage } from './features/admin/AdminFilePage'
import { AdminRoute } from './features/admin/AdminRoute'
import { AdminLayout } from './features/admin/AdminLayout'
import { ProtectedRoute } from './features/auth/ProtectedRoute'
import { LoginPage } from './features/auth/LoginPage'
import { RegisterPage } from './features/auth/RegisterPage'
import { ProfilePage } from './features/auth/ProfilePage'
import { ForgotPasswordPage } from './features/auth/ForgotPasswordPage'
import { ResetPasswordPage } from './features/auth/ResetPasswordPage'
import { PublicProfilePage } from './features/sharing/PublicProfilePage'
import { SharedViewPage } from './features/sharing/SharedViewPage'
import { SavedViewPage } from './features/sharing/SavedViewPage'
import { SavedNetworkPage } from './features/sharing/SavedNetworkPage'
import { PublicViewPage } from './features/sharing/PublicViewPage'
import { ProteinsPage } from './features/proteins/ProteinsPage'
import { LegacyProteinRedirect } from './features/proteins/LegacyProteinRedirect'
import { DownloadPage } from './features/static/DownloadPage'
import { AboutPage } from './features/static/AboutPage'
import { FAQPage } from './features/static/FAQPage'
import { ContactPage } from './features/static/ContactPage'
import { DocumentationRedirect } from './features/static/DocumentationRedirect'
import { ApiPage } from './features/static/ApiPage'
// Typed graph (F4): the typed pages' routes, each lazy behind its feature gate.
import { TYPED_ROUTES } from './features/graph/pages/typedRoutes'

const queryClient = new QueryClient()

const router = createBrowserRouter(
  [
  {
    path: '/',
    element: (
      <QueryClientProvider client={queryClient}>
        <ThemeProvider>
          <Layout />
        </ThemeProvider>
      </QueryClientProvider>
    ),
    children: [
      { index: true, element: <HomePage /> },
      { path: 'search', element: <SearchResultsPage /> },
      { path: 'search/:term', element: <SearchResultsPage /> },
      { path: 'proteins', element: <ProteinsPage /> },
      { path: 'proteins/:identifier', element: <ProteinsPage /> },
      // Superseded by /proteins/:identifier — kept so existing links resolve.
      { path: 'protein/:identifier', element: <LegacyProteinRedirect /> },
      { path: 'download', element: <DownloadPage /> },
      { path: 'about', element: <AboutPage /> },
      { path: 'faq', element: <FAQPage /> },
      { path: 'contact', element: <ContactPage /> },
      { path: 'documentation', element: <DocumentationRedirect /> },
      { path: 'developer', element: <ApiPage /> },
      { path: 'login', element: <LoginPage /> },
      { path: 'register', element: <RegisterPage /> },
      { path: 'forgot-password', element: <ForgotPasswordPage /> },
      { path: 'reset-password', element: <ResetPasswordPage /> },
      { path: 'profile', element: <ProtectedRoute><ProfilePage /></ProtectedRoute> },
      {
        path: 'profile/:username',
        element: <ProtectedRoute><PublicProfilePage /></ProtectedRoute>,
      },
      { path: 'shared/:id', element: <ProtectedRoute><SharedViewPage /></ProtectedRoute> },
      { path: 'views/:id', element: <ProtectedRoute><SavedViewPage /></ProtectedRoute> },
      { path: 'networks/:id', element: <ProtectedRoute><SavedNetworkPage /></ProtectedRoute> },
      // A public link: no login, on purpose.
      { path: 'public/:token', element: <PublicViewPage /> },
      {
        path: 'admin',
        element: (
          <AdminRoute>
            <AdminLayout />
          </AdminRoute>
        ),
        children: [
          { index: true, element: <Navigate to="settings" replace /> },
          { path: 'settings', element: <AdminSettingsPage /> },
          // Text editing now lives inside each page's settings tab; keep the
          // old link working for anyone who bookmarked it.
          { path: 'text', element: <Navigate to="/admin/settings" replace /> },
          { path: 'announcement', element: <AdminAnnouncementPage /> },
          { path: 'data', element: <AdminDataPage /> },
          { path: 'files', element: <AdminFilePage /> },
        ],
      },
      // Typed graph (F4)
      ...TYPED_ROUTES,
    ],
  },
  ],
  { basename: import.meta.env.BASE_URL }
)

export default function App() {
  return <RouterProvider router={router} />
}
