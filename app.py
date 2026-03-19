import os
import json
from flask import Flask, render_template, request, Response, stream_with_context, jsonify
from agent import review_pr_stream
from rag_tools import chat_with_codebase
from dotenv import load_dotenv
 
load_dotenv()

app = Flask(__name__)


@app.route("/")
def index():
    return render_template("index.html")


@app.route("/review", methods=["GET"])
def review():
    repo = request.args.get("repo", "").strip()
    pr = request.args.get("pr", "").strip()

    if not repo:
        return {"error": "Please provide a repository"}, 400

    pr_num = int(pr) if pr else None

    def generate():
        try:
            for chunk in review_pr_stream(repo, pr_num):
                yield chunk
        except Exception as e:
            yield f"data: {json.dumps({'event': 'error', 'message': str(e)})}\n\n"

    return Response(
        stream_with_context(generate()),
        mimetype="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"}
    )


@app.route("/chat", methods=["POST"])
def chat():
    data = request.get_json()
    question = data.get("question", "").strip()

    if not question:
        return jsonify({"error": "Please provide a question"}), 400

    try:
        answer = chat_with_codebase(question)
        return jsonify({"answer": answer})
    except Exception as e:
        return jsonify({"error": str(e)}), 500


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=7860, debug=False, threaded=True)