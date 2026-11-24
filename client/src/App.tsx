import React from 'react';
import { Routes, Route, Navigate } from 'react-router-dom';
import { Layout } from './components/Layout';
import { Dashboard } from './pages/Dashboard';
import { ConnectedAccounts } from './pages/ConnectedAccounts';
import { ContentLibrary } from './pages/ContentLibrary';
import { CreatePost } from './pages/CreatePost';
import { MediaLibrary } from './pages/MediaLibrary';
import { PublishingCalendar } from './pages/PublishingCalendar';
import { PublishingQueue } from './pages/PublishingQueue';
import { PublishingHistory } from './pages/PublishingHistory';
import { Settings } from './pages/Settings';

export const App: React.FC = () => {
  return (
    <Routes>
      <Route path="/" element={<Layout />}>
        <Route index element={<Dashboard />} />
        <Route path="accounts" element={<ConnectedAccounts />} />
        <Route path="content" element={<ContentLibrary />} />
        <Route path="create" element={<CreatePost />} />
        <Route path="media" element={<MediaLibrary />} />
        <Route path="calendar" element={<PublishingCalendar />} />
        <Route path="queue" element={<PublishingQueue />} />
        <Route path="history" element={<PublishingHistory />} />
        <Route path="settings" element={<Settings />} />
        <Route path="*" element={<Navigate to="/" replace />} />
      </Route>
    </Routes>
  );
};

export default App;
