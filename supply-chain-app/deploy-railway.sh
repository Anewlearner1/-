#!/usr/bin/env bash
# ============================================================
# Supply Chain Intelligence Platform — Railway One-Click Deploy
# 使用方式：
#   1. 安裝 Railway CLI:  npm install -g @railway/cli
#   2. 執行: bash deploy-railway.sh
# ============================================================

set -e

RAILWAY_TOKEN="${RAILWAY_TOKEN:-}"

# ── 1. 確認 railway CLI ───────────────────────────────────
if ! command -v railway &>/dev/null; then
  echo "⚙️  安裝 Railway CLI..."
  npm install -g @railway/cli
fi

# ── 2. 登入 ──────────────────────────────────────────────
if [ -z "$RAILWAY_TOKEN" ]; then
  echo ""
  echo "請輸入你的 Railway Token（到 https://railway.app/account/tokens 取得）："
  read -r -s RAILWAY_TOKEN
  export RAILWAY_TOKEN
fi

echo ""
echo "✅ 已設定 Token"

# ── 3. 建立專案 ───────────────────────────────────────────
echo ""
echo "📦 建立 Railway 專案..."
cd "$(dirname "$0")"

PROJECT_ID=$(railway project create --name "supply-chain-intelligence" --json 2>/dev/null | grep '"id"' | head -1 | awk -F'"' '{print $4}')
echo "   Project ID: $PROJECT_ID"
export RAILWAY_PROJECT_ID="$PROJECT_ID"

# ── 4. 加入 PostgreSQL ────────────────────────────────────
echo ""
echo "🐘 加入 PostgreSQL 資料庫..."
railway add --plugin postgresql 2>/dev/null || echo "   (PostgreSQL 已存在或需手動從 Railway Dashboard 加入)"

# ── 5. 部署後端 ───────────────────────────────────────────
echo ""
echo "🚀 部署後端 (FastAPI)..."
cd backend
railway up --service backend --detach
BACKEND_URL=$(railway domain --service backend 2>/dev/null || echo "")
echo "   後端 URL: https://$BACKEND_URL"

# ── 6. 設定後端環境變數 ───────────────────────────────────
echo ""
echo "🔧 設定後端環境變數..."
echo "請輸入 ANTHROPIC_API_KEY（必填）："
read -r -s ANTHROPIC_KEY
railway variables set \
  ANTHROPIC_API_KEY="$ANTHROPIC_KEY" \
  APP_ENV="production" \
  NEXTAUTH_SECRET="$(openssl rand -base64 32)" \
  --service backend

# ── 7. 部署前端 ───────────────────────────────────────────
echo ""
echo "🚀 部署前端 (Next.js)..."
cd ../frontend
railway up --service frontend --detach
FRONTEND_URL=$(railway domain --service frontend 2>/dev/null || echo "")
echo "   前端 URL: https://$FRONTEND_URL"

# ── 8. 設定前端環境變數 ───────────────────────────────────
echo ""
echo "🔧 設定前端環境變數..."
railway variables set \
  NEXT_PUBLIC_API_URL="https://$BACKEND_URL" \
  NEXTAUTH_SECRET="$(openssl rand -base64 32)" \
  NEXTAUTH_URL="https://$FRONTEND_URL" \
  --service frontend

echo ""
echo "============================================"
echo "✅ 部署完成！"
echo ""
echo "前端 APP:    https://$FRONTEND_URL"
echo "後端 API:    https://$BACKEND_URL"
echo "API 文件:   https://$BACKEND_URL/docs"
echo "============================================"
