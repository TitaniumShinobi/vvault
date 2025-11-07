# VVAULT Turnstile Configuration Guide (Chatty Clone)

## Setup Instructions

1. **Create a `.env` file** in the VVAULT root directory with your Turnstile keys:

```bash
# Cloudflare Turnstile Configuration
TURNSTILE_SITE_KEY=your-turnstile-site-key-here
TURNSTILE_SECRET_KEY=your-turnstile-secret-key-here
REACT_APP_TURNSTILE_SITE_KEY=your-turnstile-site-key-here
```

2. **Restart the development server** after adding environment variables:
```bash
npm run dev:full
```

## Getting Turnstile Keys

1. Go to [Cloudflare Dashboard](https://dash.cloudflare.com/profile/api-tokens)
2. Navigate to "Turnstile" section
3. Create a new site
4. Add your domains: `localhost:7784` and `localhost:8000`
5. Copy the Site Key and Secret Key

## Implementation Status

✅ **Frontend**: Exact Chatty Turnstile implementation cloned
✅ **Backend**: Real Turnstile verification (no test fallback)
✅ **API**: Registration endpoint with real human verification
✅ **Error Handling**: Turnstile-specific error messages
✅ **Environment Variables**: Webpack DefinePlugin configured
✅ **Production Ready**: Requires real keys to function

## Features (Cloned from Chatty)

- **Real Turnstile Widget**: Actual human verification required
- **Dark Theme**: Matches VVAULT's cinematic design
- **Compact Size**: Fits nicely in the form
- **Error Handling**: Shows specific error messages
- **Auto-cleanup**: Widget removed when switching modes
- **Backend Verification**: Server-side token validation with Cloudflare
- **Environment Variables**: Proper webpack configuration

## Code Structure (Chatty Clone)

### Frontend (CinematicLogin.js)
- **State Management**: `turnstileToken`, `turnstileWidgetId`, `turnstileError`
- **Environment Variable**: `process.env.REACT_APP_TURNSTILE_SITE_KEY`
- **Widget Lifecycle**: Load → Render → Cleanup
- **Error Handling**: Callback, error-callback, expired-callback

### Backend (vvault_web_server.py)
- **Token Verification**: Real Cloudflare API calls
- **Environment Variable**: `TURNSTILE_SECRET_KEY`
- **Error Logging**: Failed attempts logged
- **Security**: No test mode fallback

### Webpack Configuration
- **DefinePlugin**: Injects environment variables
- **Process.env**: Available in browser bundle

## Security

- **No Test Mode**: Real verification required for all registrations
- **Environment Variables**: Keys stored securely in .env file
- **Server Validation**: Backend verifies tokens with Cloudflare API
- **Error Logging**: Failed attempts logged for monitoring

## Usage

1. User switches to "Create Account" mode
2. Turnstile widget appears automatically
3. User completes real human verification
4. Form submission includes Turnstile token
5. Backend verifies token with Cloudflare
6. Registration proceeds only if verification passes

## Troubleshooting

- **Widget not appearing**: Check REACT_APP_TURNSTILE_SITE_KEY is set
- **Verification failing**: Check TURNSTILE_SECRET_KEY in .env file
- **Backend errors**: Verify TURNSTILE_SECRET_KEY is set
- **Environment variables**: Restart dev server after .env changes
- **Network issues**: Check Cloudflare API connectivity