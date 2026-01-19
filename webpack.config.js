const path = require('path');
const HtmlWebpackPlugin = require('html-webpack-plugin');
const webpack = require('webpack');

module.exports = {
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
        use: ['style-loader', 'css-loader']
      }
    ]
  },
  resolve: {
    extensions: ['.js', '.jsx']
  },
  plugins: [
    new HtmlWebpackPlugin({
      template: './public/index.html',
      title: 'VVAULT - AI Construct Memory Vault'
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
    port: 5000,
    host: '0.0.0.0',
    hot: true,
    open: false,
    historyApiFallback: true,
    allowedHosts: 'all',
    proxy: [
      {
        context: ['/api'],
        target: 'http://localhost:8000',
        changeOrigin: true,
        secure: false,
        headers: {
          'X-Forwarded-Host': 'localhost'
        }
      }
    ],
    client: {
      overlay: {
        errors: true,
        warnings: false
      },
      webSocketURL: 'auto://0.0.0.0:0/ws'
    }
  },
  devtool: 'eval-source-map'
};