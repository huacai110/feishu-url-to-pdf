#!/usr/bin/env bash
# Render.com 构建脚本
set -e

echo "===== 安装中文字体 ====="
apt-get update -qq
apt-get install -y -qq fonts-noto-cjk > /dev/null 2>&1
echo "中文字体安装完成"

echo "===== 安装 Playwright 浏览器 ====="
playwright install chromium
echo "Playwright Chromium 安装完成"

echo "===== 安装 Playwright 系统依赖 ====="
playwright install-deps chromium 2>/dev/null || echo "系统依赖已存在或跳过"

echo "===== 构建完成 ====="
