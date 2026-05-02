const path = require('path');
const HtmlWebpackPlugin = require('html-webpack-plugin');
const webpack = require('webpack');

const flaskDevTarget = (process.env.VVAULT_FLASK_PROXY_TARGET || 'http://localhost:8000').trim();
const authDevTarget = (process.env.VVAULT_AUTH_PROXY_TARGET || flaskDevTarget).trim();
const frontendHost = (process.env.VVAULT_FRONTEND_HOST || '0.0.0.0').trim();

module.exports = (_, argv = {}) => {
  const mode = argv.mode || 'development';
  const isDevelopment = mode === 'development';

  return {
    entry: './src/index.js',
    output: {
      path: path.resolve(__dirname, 'dist'),
      filename: 'bundle.[contenthash].js',
      clean: true,
      publicPath: '/'
    },
    module: {
      rules: [
        {
          test: /\.(js|jsx)$/,
          exclude: /node_modules/,
          use: {
            loader: 'babel-loader',
            options: {
              presets: ['@babel/preset-env', '@babel/preset-react']
            }
          }
        },
        {
          test: /\.css$/i,
          use: [
            {
              loader: path.resolve(__dirname, 'tools/plainCssLoader.cjs')
            }
          ]
        },
        {
          test: /\.(png|jpg|jpeg|gif|svg)$/i,
          type: 'asset/resource'
        }
      ]
    },
    resolve: {
      extensions: ['.js', '.jsx']
    },
    plugins: [
      new HtmlWebpackPlugin({
        template: './public/index.html',
        title: 'VVAULT - Vectored Anatomy Vault'
      }),
      new webpack.DefinePlugin({
        'process.env.REACT_APP_TURNSTILE_SITE_KEY': JSON.stringify(process.env.REACT_APP_TURNSTILE_SITE_KEY)
      })
    ],
    devServer: {
      static: [
        {
          directory: path.join(__dirname, 'dist'),
        },
        {
          directory: path.join(__dirname, 'assets'),
          publicPath: '/assets'
        },
        {
          directory: path.join(__dirname, 'public'),
          publicPath: '/'
        }
      ],
      compress: true,
      port: 7784,
      host: frontendHost,
      hot: true,
      open: false,
      historyApiFallback: true,
      allowedHosts: 'all',
      proxy: [
        {
          context: ['/api/auth/glyph-preview'],
          target: flaskDevTarget,
          changeOrigin: true,
          secure: false,
        },
        {
          context: ['/api/auth/google/health'],
          target: flaskDevTarget,
          changeOrigin: true,
          secure: false,
        },
        {
          context: ['/api/auth/register'],
          target: flaskDevTarget,
          changeOrigin: true,
          secure: false,
        },
        {
          context: (pathname) => pathname.startsWith('/api/auth'),
          target: authDevTarget,
          changeOrigin: true,
          secure: false,
          cookieDomainRewrite: '',
          cookiePathRewrite: '/',
        },
        {
          context: ['/api'],
          target: flaskDevTarget,
          changeOrigin: true,
          secure: false,
        },
      ],
      client: {
        overlay: {
          errors: true,
          warnings: false
        },
        webSocketURL: 'auto://0.0.0.0:0/ws'
      }
    },
    devtool: isDevelopment ? 'eval-source-map' : false
  };
};
