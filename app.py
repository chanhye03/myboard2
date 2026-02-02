# [참고] 만약 'flask'에 빨간 줄이 가고 "Import를 확인할 수 없습니다"라고 뜬다면?
# 이는 VS Code 에디터가 현재 가상환경(venv)이 아닌 기본 파이썬(Global)을 보고 있기 때문입니다.
# 이 때는 Ctrl + Shift + P를 누르고 "Python: 인터프리터 선택"을 선택한 뒤,
# 여기서 사용할 가상환경(venv)을 선택해주시면 됩니다.
# 터미널에서 서버가 정상적으로 실행된다면 코드에는 문제가 없는 상태입니다.
import os
import psycopg2
from psycopg2.extras import DictCursor
from flask import Flask, render_template, request, redirect, url_for, flash, session, jsonify
from dotenv import load_dotenv
from datetime import datetime
import json

# 로컬 환경에서는 .env를 읽고, Azure에서는 패스.
if os.path.exists('.env'):
    load_dotenv()
app = Flask(__name__)
app.secret_key = os.urandom(24)

# 데이터베이스 연결 함수
def get_db_connection():
    conn = psycopg2.connect(
        host=os.getenv('DB_HOST'),
        port=os.getenv('DB_PORT'),
        dbname=os.getenv('DB_NAME'),
        user=os.getenv('DB_USER'),
        password=os.getenv('DB_PASSWORD'),
        # sslmode='require' #Azure를 위해 반드시 추가
    )
    print('get_db_connection', conn)
    conn.autocommit = True
    return conn

@app.route('/')
def index():
    page = request.args.get('page', 1, type=int)
    per_page = 10
    offset = (page - 1) * per_page
    
    # 1. 데이터 베이스에 접속
    conn = get_db_connection()
    print('get_db_connection', conn)
    cursor = conn.cursor(cursor_factory=DictCursor)
    # 2. SELECT
    cursor.execute("SELECT COUNT(*) FROM board.posts")
    total_posts = cursor.fetchone()[0]

    cursor.execute("SELECT id, title, author, created_at, view_count, like_count FROM board.posts ORDER BY created_at DESC")
    posts = cursor.fetchall()
    cursor.close()
    conn.close()

    # 3. index.html 파일에 변수로 넘겨주기
    return render_template('index.html', posts = posts, page=page, per_page=per_page, total_posts=total_posts)

@app.route('/fms')
def fms_list():
    # 1. URL 파라미터 받기 
    page = request.args.get('page', 1, type=int)
    per_page = request.args.get('per_page', 10, type=int)
    offset = (page - 1) * per_page

    conn = get_db_connection()
    cursor = conn.cursor(cursor_factory=DictCursor)
    
    # 2. 전체 데이터 개수 가져오기
    cursor.execute("SELECT COUNT(*) FROM fms.total_result")
    total_count = cursor.fetchone()[0]

    # 3. 페이지네이션 SQL 
    query = f"""
        SELECT *
        FROM fms.total_result
        LIMIT %s OFFSET %s
    """
    cursor.execute(query, (per_page, offset))
    fms_data = cursor.fetchall()

    cursor.close()
    conn.close()

    # 4. 전체 페이지 수 계산
    total_pages = (total_count + per_page - 1) // per_page
    
    return render_template('fms_result.html', 
                           results=fms_data, 
                           total_count=total_count,
                           page=page,
                           per_page=per_page,
                           total_pages=total_pages)

@app.route('/api/chick_info/<chick_no>')
def get_chick_info(chick_no):
    conn = get_db_connection()
    cursor = conn.cursor(cursor_factory=DictCursor)
    
    # 해당 육계번호의 정보를 가져옵니다.
    query = f"""
        SELECT *
        FROM fms.chick_info a
        JOIN (SELECT code, code_desc FROM fms.master_code WHERE column_nm = 'breeds') AS b
        ON a.breeds = b.code
        WHERE chick_no = %s
    """
    cursor.execute(query, (chick_no,))
    chick_data = cursor.fetchone()
    
    cursor.close()
    conn.close()
    
    if chick_data:
        # 딕셔너리 형태로 변환하여 JSON으로 응답
        return jsonify(dict(chick_data))
    else:
        return jsonify({"error": "데이터를 찾을 수 없습니다."}), 404
    
@app.route('/dashboard')
def dashboard():
    conn = get_db_connection()
    cursor = conn.cursor(cursor_factory=DictCursor)

    # 1. 품종별 성장 효율 (산점도 - 품종별 색상 구분)
    cursor.execute("""
        SELECT m.code_desc as breed_nm, h.weight, h.feed_intake
        FROM fms.health_cond h
        JOIN fms.chick_info c ON h.chick_no = c.chick_no
        JOIN fms.master_code m ON c.breeds = m.code AND m.column_nm = 'breeds'
        WHERE h.weight > 0 AND h.feed_intake > 0
    """)
    rows1 = cursor.fetchall()
    breed_groups = {}
    for r in rows1:
        b_nm = r['breed_nm'].strip()
        if b_nm not in breed_groups: breed_groups[b_nm] = []
        breed_groups[b_nm].append({"x": float(r['feed_intake']), "y": float(r['weight'])})

    # 2. 지역별 품종 공급 현황 (Stacked Bar)
    cursor.execute("""
        SELECT s.destination, m.code_desc as breed_nm, COUNT(*) as cnt
        FROM fms.ship_result s
        JOIN fms.chick_info c ON s.chick_no = c.chick_no
        JOIN fms.master_code m ON c.breeds = m.code AND m.column_nm = 'breeds'
        GROUP BY s.destination, m.code_desc
    """)
    rows2 = cursor.fetchall()
    dest_labels = sorted(list(set(r['destination'] for r in rows2)))
    breed_names = sorted(list(set(r['breed_nm'] for r in rows2)))
    dest_matrix = {b: [0] * len(dest_labels) for b in breed_names}
    for r in rows2:
        d_idx = dest_labels.index(r['destination'])
        dest_matrix[r['breed_nm']][d_idx] = int(r['cnt'])

    # 3. 환경(온/습도)에 따른 불합격 분포 (산점도 - 커스텀 툴팁 데이터 포함)
    cursor.execute("""
        SELECT e.temp, e.humid, c.farm, h.weight
        FROM fms.prod_result p
        JOIN fms.health_cond h ON p.chick_no = h.chick_no
        JOIN fms.env_cond e ON h.check_date = e.date
        JOIN fms.chick_info c ON p.chick_no = c.chick_no
        WHERE p.pass_fail = 'F' AND h.weight > 0
    """)
    rows3 = cursor.fetchall()
    # [중요] 3번 그래프는 Scatter이므로 별도의 'labels' 변수를 보내지 않습니다.
    env_fail_data = [{"x": float(r['temp']), "y": float(r['humid']), "farm": str(r['farm']).strip(), "weight": float(r['weight'])} for r in rows3]

    # 4. 농장(Farm)별 품질 합격률 (비즈니스 인사이트)
    cursor.execute("""
        SELECT c.farm,
               COUNT(CASE WHEN p.pass_fail = 'P' THEN 1 END) * 100.0 / NULLIF(COUNT(*), 0) as pass_rate
        FROM fms.chick_info c
        JOIN fms.prod_result p ON c.chick_no = p.chick_no
        GROUP BY c.farm ORDER BY c.farm
    """)
    rows4 = cursor.fetchall()
    farm_labels = [f"{str(r['farm']).strip()} 농장" for r in rows4]
    farm_pass_rates = [round(float(r['pass_rate']), 1) if r['pass_rate'] else 0 for r in rows4]

    cursor.close()
    conn.close()

    # 파이썬에서 보내는 변수명과 HTML에서 사용하는 변수명을 완벽히 매칭
    return render_template('dashboard.html', 
                           breed_groups=breed_groups,
                           dest_labels=dest_labels, breed_names=breed_names, dest_matrix=dest_matrix,
                           env_fail_data=env_fail_data,
                           farm_labels=farm_labels, farm_pass_rates=farm_pass_rates)

@app.route('/create/', methods=['GET'] )
def create_form():
    return render_template('create.html')

@app.route('/create/',methods=['POST']  )
def create_post():
    #1. 폼에 있는 정보들을 get
    title = request.form.get('title')
    author = request.form.get('author')
    content = request.form.get('content')

    if not title or not author or not content:
        flash('모든 필드를 똑바로 채워주세요!!!!')
        return redirect(url_for('create_form'))
    
    # 1. 데이터 베이스에 접속
    conn = get_db_connection()
    cursor = conn.cursor(cursor_factory=DictCursor)
    # 2. INSERT
    cursor.execute("INSERT INTO board.posts (title, content, author) VALUES (%s, %s, %s) RETURNING id", (title,content,author))
    post_id = cursor.fetchone()[0]
    cursor.close()
    conn.close()
    flash('게시글이 성공적으로 등록되었음')
    return redirect(url_for('view_post', post_id=post_id))

@app.route('/post/<int:post_id>')
def view_post(post_id):
    conn = get_db_connection()
    cursor = conn.cursor(cursor_factory=DictCursor)
    
    cursor.execute('UPDATE board.posts SET view_count = view_count + 1 WHERE id = %s', (post_id,))
    
    cursor.execute('SELECT * FROM board.posts WHERE id = %s', (post_id,))
    post = cursor.fetchone()
    
    if post is None:
        cursor.close()
        conn.close()
        flash('게시글을 찾을 수 없습니다.')
        return redirect(url_for('index'))
    
    cursor.execute('SELECT * FROM board.comments WHERE post_id = %s ORDER BY created_at', (post_id,))
    comments = cursor.fetchall()
    
    cursor.close()
    conn.close()
    
    user_ip = request.remote_addr
    conn = get_db_connection()
    cursor = conn.cursor()
    cursor.execute('SELECT COUNT(*) FROM board.likes WHERE post_id = %s AND user_ip = %s', (post_id, user_ip))
    liked = cursor.fetchone()[0] > 0
    cursor.close()
    conn.close()
    
    return render_template('view.html', post=post, comments=comments, liked=liked)

@app.route('/edit/<int:post_id>', methods=['GET'])
def edit_form(post_id):
    conn = get_db_connection()
    cursor = conn.cursor(cursor_factory=DictCursor)
    cursor.execute('SELECT * FROM board.posts WHERE id = %s', (post_id,))
    post = cursor.fetchone()
    cursor.close()
    conn.close()
    
    if post is None:
        flash('게시글을 찾을 수 없습니다.')
        return redirect(url_for('index'))
    
    return render_template('edit.html', post=post)

@app.route('/edit/<int:post_id>', methods=['POST'])
def edit_post(post_id):
    title = request.form.get('title')
    content = request.form.get('content')
    
    if not title or not content:
        flash('제목과 내용을 모두 입력해주세요.')
        return redirect(url_for('edit_form', post_id=post_id))
    
    conn = get_db_connection()
    cursor = conn.cursor()
    cursor.execute(
        'UPDATE board.posts SET title = %s, content = %s, updated_at = %s WHERE id = %s',
        (title, content, datetime.now(), post_id)
    )
    cursor.close()
    conn.close()
    
    flash('게시글이 성공적으로 수정되었습니다.')
    return redirect(url_for('view_post', post_id=post_id))

@app.route('/delete/<int:post_id>', methods=['POST'])
def delete_post(post_id):
    conn = get_db_connection()
    cursor = conn.cursor()
    cursor.execute('DELETE FROM board.posts WHERE id = %s', (post_id,))
    cursor.close()
    conn.close()
    
    flash('게시글이 성공적으로 삭제되었습니다.')
    return redirect(url_for('index'))

@app.route('/post/comment/<int:post_id>', methods=['POST'])
def add_comment(post_id):
    author = request.form.get('author')
    content = request.form.get('content')
    
    if not author or not content:
        flash('작성자와 내용을 모두 입력해주세요.')
        return redirect(url_for('view_post', post_id=post_id))
    
    conn = get_db_connection()
    cursor = conn.cursor()
    cursor.execute(
        'INSERT INTO board.comments (post_id, author, content) VALUES (%s, %s, %s)',
        (post_id, author, content)
    )
    cursor.close()
    conn.close()
    
    flash('댓글이 등록되었습니다.')
    return redirect(url_for('view_post', post_id=post_id))

@app.route('/post/like/<int:post_id>', methods=['POST'])
def like_post(post_id):
    user_ip = request.remote_addr
    
    conn = get_db_connection()
    cursor = conn.cursor()
    cursor.execute('SELECT COUNT(*) FROM board.likes WHERE post_id = %s AND user_ip = %s', (post_id, user_ip))
    already_liked = cursor.fetchone()[0] > 0
    
    if already_liked:
        cursor.execute('DELETE FROM board.likes WHERE post_id = %s AND user_ip = %s', (post_id, user_ip))
        cursor.execute('UPDATE board.posts SET like_count = like_count - 1 WHERE id = %s', (post_id,))
        message = '좋아요가 취소되었습니다.'
    else:
        cursor.execute('INSERT INTO board.likes (post_id, user_ip) VALUES (%s, %s)', (post_id, user_ip))
        cursor.execute('UPDATE board.posts SET like_count = like_count + 1 WHERE id = %s', (post_id,))
        message = '좋아요가 등록되었습니다.'
    
    cursor.close()
    conn.close()   
    flash(message)
    return redirect(url_for('view_post', post_id=post_id))

if __name__ == '__main__':
    app.run(debug=True)

