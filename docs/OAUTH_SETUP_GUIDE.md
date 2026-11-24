# OAuth Setup Guide: Meta Graph API & Google Cloud Console

This guide provides step-by-step instructions for obtaining production and development credentials to connect **Facebook Pages** and **YouTube Channels** via official OAuth 2.0 flows.

---

## 1. Meta for Developers (Facebook Pages)

### Step 1: Create a Meta App
1. Go to [Meta for Developers](https://developers.facebook.com/) and log in with your Facebook account.
2. Click **My Apps** > **Create App**.
3. Select **Other** as the use case > click **Next**.
4. Select **Business** as the app type > click **Next**.
5. Enter an **App Display Name** (e.g. `AutoPub Studio`) and your contact email.
6. Select your Meta Business Account if applicable > click **Create App**.

### Step 2: Configure Facebook Login for Business
1. From the App Dashboard, find **Facebook Login for Business** and click **Set Up**.
2. Go to **Facebook Login for Business** > **Settings** in the left sidebar.
3. Under **Valid OAuth Redirect URIs**, enter:
   ```text
   http://localhost:3000/api/social-accounts/facebook/callback
   https://yourdomain.com/api/social-accounts/facebook/callback
   ```
4. Click **Save Changes**.

### Step 3: Retrieve App ID and App Secret
1. In the left navigation, go to **App Settings** > **Basic**.
2. Copy your **App ID**.
3. Click **Show** next to **App Secret** and copy the secret.
4. Add them to your `.env` file:
   ```env
   META_APP_ID=your_app_id_here
   META_APP_SECRET=your_app_secret_here
   META_API_VERSION=v21.0
   META_OAUTH_REDIRECT_URI=http://localhost:3000/api/social-accounts/facebook/callback
   ```

### Step 4: Required Permissions & App Review
For development, admins and testers added to your Meta App can authorize any page they manage immediately. For public users, submit the following permissions in **App Review** > **Permissions and Features**:
- `pages_show_list`: To retrieve the list of Facebook Pages managed by the user.
- `pages_read_engagement`: To inspect page details and engagement.
- `pages_manage_posts`: To publish text, photo, video, and Reel posts to the Page.
- `pages_manage_metadata`: To subscribe to webhooks and verify page integrity.

---

## 2. Google Cloud Console (YouTube Data API v3)

### Step 1: Create a Google Cloud Project
1. Navigate to the [Google Cloud Console](https://console.cloud.google.com/).
2. Click the project dropdown at the top > click **New Project**.
3. Enter Project Name (e.g. `AutoPub Studio`) > click **Create**.

### Step 2: Enable the YouTube Data API v3
1. In the sidebar, go to **APIs & Services** > **Library**.
2. Search for `YouTube Data API v3`.
3. Click on **YouTube Data API v3** > click **Enable**.

### Step 3: Configure the OAuth Consent Screen
1. Go to **APIs & Services** > **OAuth consent screen**.
2. Select **External** (or Internal for Google Workspace organizations) > click **Create**.
3. Fill in:
   - **App name**: `AutoPub Studio`
   - **User support email**: your email
   - **Developer contact information**: your email
4. Click **Save and Continue**.
5. Under **Scopes**, click **Add or Remove Scopes**, select:
   - `.../auth/youtube.upload` (Upload YouTube videos and manage your YouTube videos)
   - `.../auth/youtube.readonly` (View your YouTube account)
   - `.../auth/userinfo.profile` (View your basic profile info)
6. Under **Test Users**, add the Google email address of the YouTube channel you want to test with.

### Step 4: Create OAuth 2.0 Client ID Credentials
1. In the sidebar, go to **APIs & Services** > **Credentials**.
2. Click **Create Credentials** > select **OAuth client ID**.
3. Set **Application type** to **Web application**.
4. Set **Name** to `AutoPub Web Client`.
5. Under **Authorized redirect URIs**, add:
   ```text
   http://localhost:3000/api/social-accounts/youtube/callback
   https://yourdomain.com/api/social-accounts/youtube/callback
   ```
6. Click **Create**.
7. Copy the **Client ID** and **Client Secret**.
8. Add them to your `.env` file:
   ```env
   GOOGLE_CLIENT_ID=your_google_client_id_here.apps.googleusercontent.com
   GOOGLE_CLIENT_SECRET=your_google_client_secret_here
   GOOGLE_OAUTH_REDIRECT_URI=http://localhost:3000/api/social-accounts/youtube/callback
   ```
