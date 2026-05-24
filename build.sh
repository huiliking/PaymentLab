#!/usr/bin/env bash
set -o errexit

echo "=== Step 1: Install Python dependencies ==="
cd server
pip install --upgrade pip
pip install -r requirements.txt
pip install gunicorn
cd ..

echo "=== Step 2: Build React frontend ==="
cd client
npm install
npm run build
cd ..

echo "=== Step 3: Copy React build to server/static ==="
rm -rf server/static
cp -r client/dist server/static

echo "=== Build complete ==="