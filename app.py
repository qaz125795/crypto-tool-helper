#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Zeabur 部署入口文件
使用 Flask 作為 web server，支持 HTTP 觸發和定時任務
"""

from flask import Flask, request, jsonify
import os
import sys
from pathlib import Path

# 將當前目錄加入路徑
sys.path.insert(0, str(Path(__file__).parent))

from jackbot import (
    fetch_sector_ranking,
    fetch_whale_position,
    fetch_position_change,
    fetch_and_push_economic_data,
    fetch_all_news,
    fetch_funding_fortune_list,
    run_long_term_once
)

app = Flask(__name__)

@app.route('/', methods=['GET'])
def health_check():
    """健康檢查端點"""
    return jsonify({
        'status': 'ok',
        'message': '區塊鏈船長—傑克：自動化推播系統運行中',
        'endpoints': {
            '/': '健康檢查',
            '/sector_ranking': '主流板塊排行榜推播',
            '/whale_position': '巨鯨持倉動向',
            '/position_change': '持倉變化篩選',
            '/economic_data': '重要經濟數據推播',
            '/news': '新聞快訊推播',
            '/funding_rate': '資金費率排行榜',
            '/long_term_index': '長線牛熊導航儀',
            '/run/<task>': '執行指定任務 (sector_ranking, whale_position, long_term_index_once, etc.)'
        }
    }), 200

@app.route('/sector_ranking', methods=['GET', 'POST'])
def run_sector_ranking():
    """執行主流板塊排行榜推播"""
    try:
        fetch_sector_ranking()
        return jsonify({'status': 'success', 'message': '主流板塊排行榜推播執行成功'}), 200
    except Exception as e:
        return jsonify({'status': 'error', 'message': str(e)}), 500

@app.route('/whale_position', methods=['GET', 'POST'])
def run_whale_position():
    """執行巨鯨持倉動向"""
    try:
        fetch_whale_position()
        return jsonify({'status': 'success', 'message': '巨鯨持倉動向執行成功'}), 200
    except Exception as e:
        return jsonify({'status': 'error', 'message': str(e)}), 500

@app.route('/position_change', methods=['GET', 'POST'])
def run_position_change():
    """執行持倉變化篩選"""
    try:
        fetch_position_change()
        return jsonify({'status': 'success', 'message': '持倉變化篩選執行成功'}), 200
    except Exception as e:
        return jsonify({'status': 'error', 'message': str(e)}), 500

@app.route('/economic_data', methods=['GET', 'POST'])
def run_economic_data():
    """執行重要經濟數據推播"""
    try:
        fetch_and_push_economic_data()
        return jsonify({'status': 'success', 'message': '重要經濟數據推播執行成功'}), 200
    except Exception as e:
        return jsonify({'status': 'error', 'message': str(e)}), 500

@app.route('/news', methods=['GET', 'POST'])
def run_news():
    """執行新聞快訊推播"""
    try:
        fetch_all_news()
        return jsonify({'status': 'success', 'message': '新聞快訊推播執行成功'}), 200
    except Exception as e:
        return jsonify({'status': 'error', 'message': str(e)}), 500

@app.route('/funding_rate', methods=['GET', 'POST'])
def run_funding_rate():
    """執行資金費率排行榜"""
    try:
        fetch_funding_fortune_list()
        return jsonify({'status': 'success', 'message': '資金費率排行榜執行成功'}), 200
    except Exception as e:
        return jsonify({'status': 'error', 'message': str(e)}), 500

@app.route('/long_term_index', methods=['GET', 'POST'])
def run_long_term_index():
    """執行長線牛熊導航儀"""
    try:
        run_long_term_once()
        return jsonify({'status': 'success', 'message': '長線牛熊導航儀執行成功'}), 200
    except Exception as e:
        return jsonify({'status': 'error', 'message': str(e)}), 500

@app.route('/run/<task>', methods=['GET', 'POST'])
def run_task(task):
    """通用任務執行端點"""
    task_map = {
        'sector_ranking': fetch_sector_ranking,
        'whale_position': fetch_whale_position,
        'position_change': fetch_position_change,
        'economic_data': fetch_and_push_economic_data,
        'news': fetch_all_news,
        'funding_rate': fetch_funding_fortune_list,
        'long_term_index_once': run_long_term_once
    }
    
    if task not in task_map:
        return jsonify({
            'status': 'error',
            'message': f'未知任務: {task}',
            'available_tasks': list(task_map.keys())
        }), 400
    
    try:
        task_map[task]()
        return jsonify({'status': 'success', 'message': f'任務 {task} 執行成功'}), 200
    except Exception as e:
        return jsonify({'status': 'error', 'message': str(e)}), 500

if __name__ == '__main__':
    port = int(os.environ.get('PORT', 8080))
    # 在生產環境應該使用 gunicorn，這裡只是開發環境
    app.run(host='0.0.0.0', port=port, debug=False)

