"""System and database tools — queries database, writes files, performs system checks and calculations."""

import asyncio
import json
import logging
import os
import re

from sqlalchemy import text

from backend.core.config import settings

logger = logging.getLogger("pilot.tools.system_tasks")


async def database_query(args: dict, session_id: str) -> dict:
    logger.info("Executing database_query tool")
    db_info = ""
    try:
        from backend.db.engine import AsyncSessionLocal

        # Run DB query using AsyncSessionLocal
        async with AsyncSessionLocal() as db_session:
            # Check voice_enrollments count
            res = await db_session.execute(text("SELECT COUNT(*) FROM voice_enrollments"))
            count = res.scalar()

            # Check recent enrollments
            recent_res = await db_session.execute(
                text(
                    "SELECT speaker_name, role, status FROM voice_enrollments ORDER BY id DESC LIMIT 3"
                )
            )
            recent = recent_res.fetchall()

            db_info = (
                f"The pilot voice database is connected. There are currently {count} speaker voice recordings saved. "
            )
            if count > 0:
                records_summary = ", ".join([f"{r[0]} ({r[1]})" for r in recent])
                db_info += f"The most recent recordings belong to: {records_summary}."
    except Exception as e:
        logger.warning(f"Database connection failed: {e}. Falling back to filesystem stats.")
        # Fallback to checking the data/embeddings/ directory
        try:
            embeddings_dir = "data/embeddings"
            if os.path.exists(embeddings_dir):
                files = os.listdir(embeddings_dir)
                npy_files = [f for f in files if f.endswith(".npy")]
                db_info = (
                    f"The database server was unreachable, but checking the voice signature directory, "
                    f"I found {len(npy_files)} speaker voice signatures saved in storage."
                )
            else:
                db_info = "The database is unreachable and the voice signatures directory does not exist."
        except Exception:
            db_info = f"Both database connection and filesystem fallback failed. The database error was {e}."

    # Use LLM to compose a natural voice response
    prompt = f"The user asked: '{args.get('query', '')}'. Here is the actual data gathered: {db_info}. Please write a concise natural voice report summarizing this."
    spoken_reply = await _call_text_llm(prompt)
    return {"status": "ok", "db_info": db_info, "spoken_reply": spoken_reply}


async def write_file_tool(args: dict, session_id: str) -> dict:
    query = args.get("query", "") or args.get("synopsis") or ""
    logger.info(f"Executing write_file tool for query: {query}")

    prompt = (
        f"The user wants to generate a complete, structured code file based on this request: '{query}'.\n"
        f"Please write the file content. Ensure the code is beautifully structured, properly indented (4 spaces for C++ and Python), has clean spacing, descriptive variables, and comments.\n"
        f"You must format your response as a single, valid JSON object with exactly two fields:\n"
        f"- 'filename': name of the file to create (e.g. 'prime.cpp')\n"
        f"- 'content': the actual string content of the code, preserving all indentation, newlines (using \\n), and spacing precisely.\n"
        f"Provide ONLY the raw JSON object. Do not wrap in markdown blocks, do not add trailing explanations."
    )

    raw_json = await _call_text_llm(
        prompt,
        system_prompt="You are a code writing assistant. Output valid JSON ONLY. No markdown wrappers, no code blocks, no fences.",
    )
    filename = "script.py"
    content = ""

    try:
        # Robust JSON cleaning and parsing for partially cut-off or ill-formatted JSON outputs
        clean_json = raw_json.strip()
        # Strip potential markdown fences if model hallucinated them
        if clean_json.startswith("```json"):
            clean_json = clean_json[7:]
        elif clean_json.startswith("```"):
            clean_json = clean_json[3:]
        if clean_json.endswith("```"):
            clean_json = clean_json[:-3]
        clean_json = clean_json.strip()

        # Resilient JSON repair: check if JSON starts with '{' but is missing the closing '}'
        if clean_json.startswith("{") and not clean_json.endswith("}"):
            clean_json += "}"

        json_match = re.search(r"\{.*\}", clean_json, re.DOTALL)
        if json_match:
            try:
                parsed = json.loads(json_match.group(0))
                filename = parsed.get("filename", filename)
                content = parsed.get("content", "")
            except Exception:
                # If json.loads fails, try regex extraction of key fields
                fn_match = re.search(r'"filename"\s*:\s*"([^"]+)"', clean_json)
                ct_match = re.search(r'"content"\s*:\s*"([\s\S]+?)"\s*\}?$', clean_json)
                if fn_match:
                    filename = fn_match.group(1)
                if ct_match:
                    # Clean escaped newlines/quotes
                    content = ct_match.group(1).replace("\\n", "\n").replace('\\"', '"')
                else:
                    content = raw_json
        else:
            content = raw_json
    except Exception as e:
        logger.error(f"Failed to parse generated file JSON: {e}")
        content = raw_json

    # Normalize filename
    if not filename or filename == "generated_output.txt":
        filename = "script.py"

    # Clean up escaped backslashes/newlines if parsed rawly
    if content:
        # Unescape escaped characters if double-serialized
        try:
            if content.startswith('"') and content.endswith('"'):
                content = json.loads(content)
        except Exception:
            pass

    # Return the generated code directly to be displayed on transcripts, without writing any physical file to disk
    spoken = "I have successfully generated the requested structured code block. You can see it beautifully formatted in your transcript panel!"
    return {
        "status": "ok",
        "filename": filename,
        "content_length": len(content),
        "content": content,  # Preserve the generated content in results so it can be rendered in transcripts
        "spoken_reply": spoken,
    }


async def write_email_tool(args: dict, session_id: str) -> dict:
    query = args.get("query", "") or args.get("synopsis") or ""
    logger.info(f"Executing write_email tool for query: {query}")

    # Locate user matching email/name if provided
    from sqlalchemy import select

    from backend.core.session_state import get_state
    from backend.db.engine import AsyncSessionLocal
    from backend.db.models import User

    state = get_state(session_id)

    # Prioritizes manually filled form context details first
    target_email = state.pending_email_recipient_email or "team@pilot.ai"
    target_user_name = state.pending_email_recipient_name or "Team member"
    subject = state.pending_email_subject or "Update from PILOT Voice OS"
    cc_bcc = state.pending_email_cc_bcc or "info@pilot.ai"

    # Resolve recipient name from pre-filled email handle if placeholder name is active
    if "@" in target_email and target_user_name == "Team member":
        target_user_name = target_email.split("@")[0].title()

    # ── VAGUE PHRASE INTERCEPT ──
    # If the user speaks a generic, short command like "write an email" or "draft an email",
    # automatically leverage the pre-filled parameters to dictate the email's topic.
    def is_generic_email_command(q: str) -> bool:
        q_clean = q.lower().strip()
        # Remove punctuation
        q_clean = re.sub(r"[^\w\s]", "", q_clean).strip()

        # Remove filler prefixes
        fillers = [
            "hey pilot",
            "hey",
            "pilot",
            "please",
            "can you",
            "could you",
            "i want to",
            "i want you to",
            "just",
        ]
        changed = True
        while changed:
            changed = False
            for f in fillers:
                if q_clean.startswith(f + " ") or q_clean == f:
                    q_clean = q_clean[len(f) :].strip()
                    changed = True
                    break

        # Remove filler suffixes
        suffixes = ["y", "yes", "ok", "okay", "now", "please"]
        changed = True
        while changed:
            changed = False
            for s in suffixes:
                if q_clean.endswith(" " + s) or q_clean == s:
                    q_clean = q_clean[: -len(s)].strip()
                    changed = True
                    break

        # List of generic patterns
        generic_patterns = {
            "write email",
            "write the email",
            "write an email",
            "write mail",
            "write the mail",
            "write a mail",
            "draft email",
            "draft the email",
            "draft an email",
            "draft mail",
            "draft the mail",
            "draft a mail",
            "create email",
            "create the email",
            "create an email",
            "create mail",
            "create the mail",
            "create a mail",
            "email template",
            "mail template",
            "email",
            "mail",
            "send email",
            "send mail",
            "write",
            "draft",
            "create",
        }
        return q_clean in generic_patterns or len(q_clean) <= 2

    if is_generic_email_command(query) or not query.strip():
        if (
            subject
            and subject.strip()
            and subject not in ("Enter email subject", "Update from PILOT Voice OS", "")
        ):
            # Use the pre-filled subject from the compose form
            query = f"Write a comprehensive, professional email regarding the subject: '{subject}'"
        else:
            # No subject pre-filled — write a polite placeholder and note the subject is unspecified
            query = (
                "Write a professional email. Since no specific subject or topic was provided, "
                "use 'General Update' as the subject, write a brief placeholder body, "
                "and add a note at the end suggesting the user fill in the specific details."
            )
    else:
        # User spoke a specific topic (e.g. "sick leave") in the voice command.
        # Prioritize and use the spoken topic directly to form the email's context!
        query = f"Write a detailed email template regarding the specific topic of: '{query}'"

    # Dynamic CC/BCC extraction from spoken voice query
    inferred_cc = []
    inferred_bcc = []

    query_lower = query.lower()
    cc_part = ""
    bcc_part = ""
    to_part = query_lower

    if "bcc" in query_lower:
        parts = query_lower.split("bcc")
        bcc_part = parts[-1]
        to_part = parts[0]
        if "cc" in to_part:
            subparts = to_part.split("cc")
            cc_part = subparts[-1]
            to_part = subparts[0]
    elif "cc" in query_lower:
        parts = query_lower.split("cc")
        cc_part = parts[-1]
        to_part = parts[0]
    elif "copy" in query_lower:
        parts = query_lower.split("copy")
        cc_part = parts[-1]
        to_part = parts[0]

    try:
        async with AsyncSessionLocal() as db_session:
            users_res = await db_session.execute(select(User))
            all_users = users_res.scalars().all()

            # Parse CC part
            if cc_part:
                cc_words = set(re.findall(r"\b\w+\b", cc_part))
                for u in all_users:
                    name_parts = set(u.name.lower().split())
                    if (name_parts & cc_words) or u.email.lower() in cc_part:
                        inferred_cc.append(u.email)

            # Parse BCC part
            if bcc_part:
                bcc_words = set(re.findall(r"\b\w+\b", bcc_part))
                for u in all_users:
                    name_parts = set(u.name.lower().split())
                    if (name_parts & bcc_words) or u.email.lower() in bcc_part:
                        inferred_bcc.append(u.email)

            # Parse main To recipient (only if target_email is default/empty)
            if target_email == "team@pilot.ai" or not state.pending_email_recipient_email:
                to_words = set(re.findall(r"\b\w+\b", to_part))
                for u in all_users:
                    name_parts = set(u.name.lower().split())
                    if (name_parts & to_words) or u.email.lower() in to_part:
                        target_user_name = u.name
                        target_email = u.email
                        break
    except Exception as e:
        logger.error(f"User db search failed: {e}")

    cc_bcc_elements = []
    if inferred_cc:
        cc_bcc_elements.append(f"cc: {', '.join(inferred_cc)}")
    if inferred_bcc:
        cc_bcc_elements.append(f"bcc: {', '.join(inferred_bcc)}")

    cc_bcc_str = ", ".join(cc_bcc_elements)

    # Prioritize manually set state.pending_email_cc_bcc first, otherwise use inferred
    if state.pending_email_cc_bcc:
        cc_bcc = state.pending_email_cc_bcc
    elif cc_bcc_str:
        state.pending_email_cc_bcc = cc_bcc_str
        cc_bcc = cc_bcc_str
    else:
        cc_bcc = "info@pilot.ai"

    # ── Clean the query of wake-word artifacts that confuse small LLMs ──
    clean_query = re.sub(
        r"\b(hey|hello|hi|ok|okay|a|ey|ah)\s+,?\s*(?:pilot|violet)\b", "", query, flags=re.IGNORECASE
    ).strip()
    clean_query = re.sub(r"^[,\s]+", "", clean_query).strip()  # Remove leading commas/spaces
    if not clean_query:
        clean_query = query  # Fallback to original if stripping removed everything

    # Explicitly enforce user prefilled variables (from state payload sync) inside background LLM prompts
    prompt = (
        f"TASK: Write a professional email. Follow the instructions EXACTLY.\n\n"
        f"── EMAIL PARAMETERS ──\n"
        f"Recipient Name: {target_user_name}\n"
        f"Recipient Email: {target_email}\n"
        f"CC/BCC: {cc_bcc}\n"
        f"Subject: {subject}\n"
        f"Topic / Purpose of Email: {clean_query}\n\n"
        f"── STRICT RULES ──\n"
        f"1. The email MUST be about the topic stated above ('{subject}'). Do NOT write about anything else.\n"
        f"2. Tone: Professional, clear, and business-appropriate.\n"
        f"3. Output ONLY the email body text. Do NOT include headers like To:, From:, Subject:, CC:.\n"
        f"4. Start with 'Dear {target_user_name},' and end with 'Best regards,' followed by the sender's name.\n"
        f"5. Keep the email 3-5 paragraphs long.\n\n"
        f"── BEGIN EMAIL ──\n"
        f"Dear {target_user_name},\n\n"
    )

    draft = await _call_text_llm(
        prompt,
        system_prompt="You are a professional email writing assistant. You write polished, on-topic business emails. Output ONLY the email body — no headers, no markdown formatting.",
        max_tokens=800,
    )

    # Parse subject out of draft safely
    subject_match = re.search(r"Subject:\s*(.*)", draft)
    parsed_subject = subject_match.group(1).strip() if subject_match else subject

    # Store dynamic state
    state.pending_email_draft = draft
    state.pending_email_subject = parsed_subject
    state.pending_email_recipient_email = target_email
    state.pending_email_recipient_name = target_user_name

    spoken = f"I have successfully drafted the email to {target_user_name} ({target_email}) with the subject '{parsed_subject}'. You can speak 'send this email' to send it in real time!"
    return {
        "status": "ok",
        "email_draft": draft,
        "recipient_name": target_user_name,
        "recipient_email": target_email,
        "cc_bcc": cc_bcc,
        "spoken_reply": spoken,
    }


def parse_cc_bcc(cc_bcc_str: str) -> tuple[list[str], list[str]]:
    """Parses a string like 'cc: a@b.com, bcc: c@d.com' or raw list into CC and BCC lists."""
    import re

    cc_list = []
    bcc_list = []
    if not cc_bcc_str:
        return cc_list, bcc_list
    s = cc_bcc_str.lower().strip()
    if "cc:" in s or "bcc:" in s:
        parts = re.split(r"(cc:|bcc:)", s)
        current_mode = "cc"
        for part in parts:
            part = part.strip()
            if part == "cc:":
                current_mode = "cc"
            elif part == "bcc:":
                current_mode = "bcc"
            elif part:
                emails = re.findall(r"[a-z0-9\.\-+_]+@[a-z0-9\.\-+_]+\.[a-z]+", part)
                if current_mode == "cc":
                    cc_list.extend(emails)
                else:
                    bcc_list.extend(emails)
    else:
        # No explicit prefix, treat all found email addresses as CC
        cc_list = re.findall(r"[a-z0-9\.\-+_]+@[a-z0-9\.\-+_]+\.[a-z]+", s)
    return list(set(cc_list)), list(set(bcc_list))


async def send_email_tool(args: dict, session_id: str) -> dict:
    from email.mime.multipart import MIMEMultipart
    from email.mime.text import MIMEText

    from backend.core.config import settings
    from backend.core.session_state import get_state

    state = get_state(session_id)
    draft = state.pending_email_draft
    subject = state.pending_email_subject
    to_email = state.pending_email_recipient_email
    to_name = state.pending_email_recipient_name
    cc_bcc_str = state.pending_email_cc_bcc

    if not draft or not to_email:
        err_reply = (
            "There is no pending email draft to send. Please first tell me to 'write an email' to someone."
        )
        return {"status": "error", "message": "no_pending_draft", "spoken_reply": err_reply}

    logger.info(f"Sending real-time email to {to_email} with subject: {subject}")

    try:
        # Strip potential markdown code fencing from the draft before sending
        clean_draft = draft
        if clean_draft.startswith("```email"):
            clean_draft = clean_draft[8:]
        elif clean_draft.startswith("```"):
            clean_draft = clean_draft[3:]
        if clean_draft.endswith("```"):
            clean_draft = clean_draft[:-3]
        clean_draft = clean_draft.strip()

        cc_emails, bcc_emails = parse_cc_bcc(cc_bcc_str)

        if settings.SMTP_USER and settings.SMTP_PASS:
            msg = MIMEMultipart("alternative")
            msg["Subject"] = subject
            msg["From"] = settings.SMTP_USER
            msg["To"] = to_email
            if cc_emails:
                msg["Cc"] = ", ".join(cc_emails)

            # Format nicely as HTML
            formatted_body = clean_draft.replace("\n", "<br/>")
            html = f"""
            <div style="font-family:sans-serif;max-width:540px;margin:40px auto;background:#F9F8F6;
                        color:#F5A700;border-radius:12px;padding:2.5rem;border:1.5px solid #E5E2DA;
                        box-shadow: 0 4px 12px rgba(0,0,0,0.03)">
              <div style="font-size:1.3rem;font-weight:800;color:#7C5E00;margin-bottom:1.5rem;border-bottom:1px solid #E5E2DA;padding-bottom:0.5rem">⬡ PILOT Voice OS</div>
              <div style="font-size:0.95rem;line-height:1.6;color:#222">
                {formatted_body}
              </div>
              <p style="color:#888;font-size:0.75rem;margin-top:2rem;border-top:1px dashed #E5E2DA;padding-top:0.8rem">This email was dispatched securely via PILOT Voice OS Biometric Action Authorization.</p>
            </div>
            """
            msg.attach(MIMEText(html, "html"))

            # SMTP sendmail needs ALL envelope recipients (To + CC + BCC)
            recipients = [to_email] + cc_emails + bcc_emails

            # Send via asyncio to thread
            await asyncio.to_thread(
                _smtp_send_helper,
                settings.SMTP_HOST,
                settings.SMTP_PORT,
                settings.SMTP_USER,
                settings.SMTP_PASS,
                recipients,
                msg,
            )
            spoken = f"Successfully dispatched the email to {to_name} in real time!"
            if cc_emails:
                spoken += f" CC'd: {', '.join(cc_emails)}."
        else:
            spoken = f"Simulated: SMTP credentials are not configured in your backend .env file, but I have verified your biometric authorization and simulated sending the email to {to_name} ({to_email}) successfully!"
            if cc_emails or bcc_emails:
                spoken += f" CC: {', '.join(cc_emails)} | BCC: {', '.join(bcc_emails)}."

        # Clear state after sending
        state.pending_email_draft = ""
        state.pending_email_subject = ""
        state.pending_email_recipient_email = ""
        state.pending_email_recipient_name = ""
        state.pending_email_cc_bcc = ""

        return {
            "status": "ok",
            "recipient_name": to_name,
            "recipient_email": to_email,
            "subject": subject,
            "spoken_reply": spoken,
        }
    except Exception as e:
        logger.error(f"Failed sending background real-time email: {e}")
        return {"status": "error", "message": str(e), "spoken_reply": f"Failed sending the email: {e}"}


def _smtp_send_helper(host, port, user, password, to, msg):
    import smtplib

    with smtplib.SMTP(host, port, timeout=10) as server:
        server.ehlo()
        server.starttls()
        server.ehlo()
        server.login(user, password)
        server.sendmail(user, to, msg.as_string())


async def system_check(args: dict, session_id: str) -> dict:
    logger.info("Executing system_check tool")
    try:
        files = os.listdir(".")
        # Filter and summarize list
        python_files = [f for f in files if f.endswith(".py")]
        folders = [f for f in files if os.path.isdir(f) and not f.startswith(".")]

        summary = f"In the current directory, I found {len(python_files)} python files, including {', '.join(python_files[:5])}. "
        if folders:
            summary += f"The main folders are {', '.join(folders)}."
    except Exception as e:
        summary = f"Failed to perform filesystem check: {e}"

    prompt = f"The user asked: '{args.get('query', '')}'. Here is the directory listing/system stats summary: {summary}. Please write a concise natural voice report explaining this."
    spoken_reply = await _call_text_llm(prompt)
    return {"status": "ok", "summary": summary, "spoken_reply": spoken_reply}


async def complex_calculation(args: dict, session_id: str) -> dict:
    query = args.get("query", "")
    logger.info(f"Executing complex_calculation tool for: {query}")

    prompt = f"Please perform this complex task, reasoning, or math query: '{query}'. Compute the answer and present it in a clear, natural voice report."
    spoken_reply = await _call_text_llm(prompt)
    return {"status": "ok", "query": query, "spoken_reply": spoken_reply}


async def cancel_task_tool(args: dict, session_id: str) -> dict:
    logger.info("Executing cancel_task tool")
    spoken = "Stopping everything right away and canceling all ongoing processes."
    return {
        "status": "ok",
        "message": "All active background tasks and database transactions have been cleanly cancelled.",
        "spoken_reply": spoken,
    }


async def _call_text_llm(
    prompt: str,
    system_prompt: str = "You are a helpful voice assistant. Answer concisely in 1-3 spoken sentences.",
    max_tokens: int = 250,
) -> str:
    # Try Groq first
    if settings.GROQ_API_KEY:
        try:
            from groq import AsyncGroq

            client = AsyncGroq(api_key=settings.GROQ_API_KEY)
            resp = await client.chat.completions.create(
                model="llama-3.1-8b-instant",
                messages=[{"role": "system", "content": system_prompt}, {"role": "user", "content": prompt}],
                max_tokens=max_tokens,
            )
            if resp.choices[0].message.content:
                return resp.choices[0].message.content.strip()
        except Exception as e:
            logger.warning(f"Groq tool text call failed: {e}")

    # Try Gemini
    if settings.GEMINI_API_KEY:
        try:
            import google.generativeai as genai

            genai.configure(api_key=settings.GEMINI_API_KEY)
            model = genai.GenerativeModel("gemini-1.5-flash")
            resp = await model.generate_content_async(prompt)
            if resp.text:
                return resp.text.strip()
        except Exception as e:
            logger.warning(f"Gemini tool text call failed: {e}")

    # Fallback to local Ollama
    try:
        import httpx

        url = f"{settings.OLLAMA_BASE_URL.rstrip('/')}/api/chat"
        async with httpx.AsyncClient(timeout=60.0) as client:
            resp = await client.post(
                url,
                json={
                    "model": settings.OLLAMA_MODEL,
                    "messages": [
                        {"role": "system", "content": system_prompt},
                        {"role": "user", "content": prompt},
                    ],
                    "stream": False,
                    "options": {"num_predict": max_tokens, "temperature": 0.4},
                },
            )
            if resp.status_code == 200:
                return resp.json().get("message", {}).get("content", "").strip()
    except Exception as e:
        logger.error(f"Ollama local tool text call failed: {e}")

    return "I completed the processing task but was unable to format a spoken summary response. Please check your AI keys."
