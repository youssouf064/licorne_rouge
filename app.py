from flask import Flask, render_template, request, redirect, session
import sqlite3
from datetime import datetime
from flask_bcrypt import Bcrypt
import os
from werkzeug.utils import secure_filename

app = Flask(__name__)
app.secret_key = "secret123"
bcrypt = Bcrypt(app)

UPLOAD_FOLDER = "static/images"
os.makedirs(UPLOAD_FOLDER, exist_ok=True)
app.config["UPLOAD_FOLDER"] = UPLOAD_FOLDER


# ---------------- DATABASE ----------------
def get_db():
    conn = sqlite3.connect("database.db")
    conn.row_factory = sqlite3.Row
    return conn


def init_db():
    con = sqlite3.connect("database.db")

    con.execute("""
    CREATE TABLE IF NOT EXISTS users(
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        username TEXT,
        email TEXT,
        password TEXT,
        profile_pic TEXT,
        online INTEGER DEFAULT 0
    )
    """)

    con.execute("""
    CREATE TABLE IF NOT EXISTS posts(
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        user_id INTEGER,
        content TEXT,
        image TEXT,
        date TEXT
    )
    """)

    con.execute("""
    CREATE TABLE IF NOT EXISTS comments(
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        user_id INTEGER,
        post_id INTEGER,
        content TEXT,
        date TEXT
    )
    """)

    con.execute("""
    CREATE TABLE IF NOT EXISTS likes(
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        user_id INTEGER,
        post_id INTEGER
    )
    """)

    con.execute("""
    CREATE TABLE IF NOT EXISTS messages(
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        sender_id INTEGER,
        receiver_id INTEGER,
        content TEXT,
        date TEXT
    )
    """)

    con.execute("""
    CREATE TABLE IF NOT EXISTS followers(
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        follower_id INTEGER,
        following_id INTEGER
    )
    """)

    con.execute("""
    CREATE TABLE IF NOT EXISTS notifications(
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        user_id INTEGER,
        message TEXT
    )
    """)

    con.commit()
    con.close()


# ---------------- HOME ----------------
@app.route("/")
def index():
    if "user_id" not in session:
        return redirect("/login")

    con = get_db()

    posts = con.execute("""
        SELECT posts.*, users.username, users.profile_pic,
        (SELECT COUNT(*) FROM likes WHERE post_id = posts.id) AS like_count
        FROM posts
        JOIN users ON posts.user_id = users.id
        ORDER BY posts.id DESC
    """).fetchall()

    comments = con.execute("""
        SELECT comments.*, users.username
        FROM comments
        JOIN users ON comments.user_id = users.id
    """).fetchall()

    return render_template("index.html", posts=posts, comments=comments)


# ---------------- REGISTER ----------------
@app.route("/register", methods=["GET","POST"])
def register():
    if request.method == "POST":

        username = request.form["username"]
        email = request.form["email"]
        password = bcrypt.generate_password_hash(request.form["password"]).decode("utf-8")

        profile_pic = request.files.get("profile_pic")
        filename = None

        if profile_pic and profile_pic.filename != "":
            filename = secure_filename(profile_pic.filename)
            profile_pic.save(os.path.join(app.config["UPLOAD_FOLDER"], filename))

        con = get_db()

        con.execute(
            "INSERT INTO users(username,email,password,profile_pic) VALUES(?,?,?,?)",
            (username,email,password,filename)
        )

        con.commit()

        return redirect("/login")

    return render_template("register.html")


# ---------------- LOGIN ----------------
@app.route("/login", methods=["GET","POST"])
def login():

    if request.method == "POST":

        email = request.form["email"]
        password = request.form["password"]

        con = get_db()

        user = con.execute(
            "SELECT * FROM users WHERE email=?",
            (email,)
        ).fetchone()

        if user and bcrypt.check_password_hash(user["password"], password):

            session["user_id"] = user["id"]
            session["username"] = user["username"]

            con.execute("UPDATE users SET online=1 WHERE id=?", (user["id"],))
            con.commit()

            return redirect("/")

        else:
            return "Email ou mot de passe incorrect"

    return render_template("login.html")


# ---------------- LOGOUT ----------------
@app.route("/logout")
def logout():

    if "user_id" in session:
        con = get_db()
        con.execute("UPDATE users SET online=0 WHERE id=?", (session["user_id"],))
        con.commit()

    session.clear()

    return redirect("/")


# ---------------- CREATE POST ----------------
@app.route("/create_post", methods=["POST"])
def create_post():

    if "user_id" not in session:
        return redirect("/login")

    content = request.form["content"]

    image = request.files.get("image")

    filename = None

    if image and image.filename != "":
        filename = secure_filename(image.filename)
        image.save(os.path.join(app.config["UPLOAD_FOLDER"], filename))

    con = get_db()

    con.execute(
        "INSERT INTO posts(user_id,content,image,date) VALUES(?,?,?,?)",
        (session["user_id"],content,filename,datetime.now().strftime("%Y-%m-%d %H:%M:%S"))
    )

    con.commit()

    return redirect("/")


# ---------------- LIKE ----------------
@app.route("/like/<int:post_id>")
def like(post_id):

    if "user_id" not in session:
        return redirect("/login")

    con = get_db()

    con.execute(
        "INSERT INTO likes(user_id,post_id) VALUES(?,?)",
        (session["user_id"],post_id)
    )

    con.commit()

    return redirect("/")


# ---------------- COMMENT ----------------
@app.route("/comment/<int:post_id>", methods=["POST"])
def comment(post_id):

    if "user_id" not in session:
        return redirect("/login")

    content = request.form["content"]

    con = get_db()

    con.execute(
        "INSERT INTO comments(user_id,post_id,content,date) VALUES(?,?,?,?)",
        (session["user_id"],post_id,content,datetime.now().strftime("%Y-%m-%d %H:%M:%S"))
    )

    con.commit()

    return redirect("/")


# ---------------- MESSAGES ----------------
@app.route("/messages/<int:user_id>", methods=["GET","POST"])
def messages(user_id):

    if "user_id" not in session:
        return redirect("/login")

    con = get_db()

    if request.method == "POST":

        content = request.form["content"]

        con.execute(
            "INSERT INTO messages(sender_id,receiver_id,content,date) VALUES(?,?,?,?)",
            (session["user_id"],user_id,content,datetime.now().strftime("%Y-%m-%d %H:%M:%S"))
        )

        con.commit()

    msgs = con.execute("""
        SELECT messages.*, users.username
        FROM messages
        JOIN users ON messages.sender_id = users.id
        WHERE (sender_id=? AND receiver_id=?)
        OR (sender_id=? AND receiver_id=?)
        ORDER BY id
    """,(session["user_id"],user_id,user_id,session["user_id"])).fetchall()

    return render_template("messages.html",msgs=msgs,user_id=user_id)


# ---------------- CONVERSATIONS ----------------
@app.route("/conversations")
def conversations():

    if "user_id" not in session:
        return redirect("/login")

    con = get_db()

    users = con.execute("""
    SELECT id,username,profile_pic
    FROM users
    WHERE id != ?
    """,(session["user_id"],)).fetchall()

    return render_template("conversations.html",users=users)


# ---------------- PROFILE ----------------
@app.route("/profile/<int:user_id>")
def profile(user_id):

    if "user_id" not in session:
        return redirect("/login")

    con = get_db()

    user = con.execute("SELECT * FROM users WHERE id=?",(user_id,)).fetchone()

    posts = con.execute(
        "SELECT * FROM posts WHERE user_id=? ORDER BY id DESC",
        (user_id,)
    ).fetchall()

    return render_template("profile.html",user=user,posts=posts)


# ---------------- NOTIFICATIONS ----------------
@app.route("/notifications")
def notifications():

    if "user_id" not in session:
        return redirect("/login")

    con = get_db()

    notes = con.execute(
        "SELECT * FROM notifications WHERE user_id=?",
        (session["user_id"],)
    ).fetchall()

    return render_template("notifications.html",notes=notes)


# ---------------- RUN ----------------
if __name__ == "__main__":
    init_db()
    app.run()