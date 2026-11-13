import os

# --- 1. UPDATE src/01_ai_content.py ---
ai_path = r"d:\Dat\App_Video\src\01_ai_content.py"
with open(ai_path, "r", encoding="utf-8") as f:
    c_ai = f.read()

cr = "\r\n" if "\r\n" in c_ai else "\n"

# In 01_ai_content.py: update avatar assignment and tempo in comments loop
old_casting = (
    '    female_keywords = {"linh", "trang", "mai", "be", "hoa", "ngoc", "lan", "anh", "girl", "cute", "thao", "vy", "my", "phuong", "giang", "nhung", "quynh", "huyen", "nga"}' + cr +
    '    male_keywords = {"nam", "tuan", "duc", "boy", "dev", "minh", "hoang", "khoa", "huy", "kien", "dat", "quan", "hung", "long", "thanh", "hai", "son", "dung"}' + cr +
    cr +
    '    comments = []' + cr +
    '    raw_comments = data.get("comments", [])'
)

new_casting = (
    '    female_keywords = {"linh", "trang", "mai", "be", "hoa", "ngoc", "lan", "anh", "girl", "cute", "thao", "vy", "my", "phuong", "giang", "nhung", "quynh", "huyen", "nga"}' + cr +
    '    male_keywords = {"nam", "tuan", "duc", "boy", "dev", "minh", "hoang", "khoa", "huy", "kien", "dat", "quan", "hung", "long", "thanh", "hai", "son", "dung"}' + cr +
    cr +
    '    # Phân nhóm kho avatar người thật theo bối cảnh câu chuyện' + cr +
    '    ctx_type = data.get("story_context_type", "general_social")' + cr +
    '    if ctx_type == "office_workplace":' + cr +
    '        f_avatar_pool = ["female_office_1.jpg", "female_office_2.jpg", "female_office_3.jpg"]' + cr +
    '        m_avatar_pool = ["male_office_1.jpg", "male_office_2.jpg", "male_office_3.jpg"]' + cr +
    '    elif ctx_type == "student_youth":' + cr +
    '        f_avatar_pool = ["female_student_1.jpg", "female_student_2.jpg", "female_student_3.jpg"]' + cr +
    '        m_avatar_pool = ["male_student_1.jpg", "male_student_2.jpg", "male_student_3.jpg"]' + cr +
    '    else:' + cr +
    '        f_avatar_pool = ["female_office_1.jpg", "female_office_2.jpg", "female_student_1.jpg", "female_student_2.jpg", "female_student_3.jpg", "female_office_3.jpg"]' + cr +
    '        m_avatar_pool = ["male_office_1.jpg", "male_office_2.jpg", "male_student_1.jpg", "male_student_2.jpg", "male_student_3.jpg", "male_office_3.jpg"]' + cr +
    cr +
    '    comments = []' + cr +
    '    raw_comments = data.get("comments", [])'
)

if old_casting in c_ai:
    c_ai = c_ai.replace(old_casting, new_casting, 1)
    print("Updated avatar pools in 01_ai_content.py successfully.")
else:
    print("ERROR: old_casting not found in 01_ai_content.py!")

# Assign avatar in comment instantiation
old_cmt_inst = (
    '        comments.append(Comment(' + cr +
    '            username=c.get("username", f"user_{idx}"),' + cr +
    '            content=c.get("content", ""), ' + cr +
    '            reply_to=c.get("reply_to", "") or data.get("username", "author"),' + cr +
    '            avatar_color=clean_hex_color(c.get("avatar_color", "")),'
)
if old_cmt_inst not in c_ai:
    old_cmt_inst = (
        '        comments.append(Comment(' + cr +
        '            username=c.get("username", f"user_{idx}"),' + cr +
        '            content=c.get("content", ""),' + cr +
        '            reply_to=c.get("reply_to", "") or data.get("username", "author"),' + cr +
        '            avatar_color=clean_hex_color(c.get("avatar_color", "")),'
    )

new_cmt_inst = (
    '        chosen_c_pool = f_avatar_pool if is_female else m_avatar_pool' + cr +
    '        c_avatar_file = chosen_c_pool[(idx - 1) % len(chosen_c_pool)]' + cr +
    '        comments.append(Comment(' + cr +
    '            username=c.get("username", f"user_{idx}"),' + cr +
    '            content=c.get("content", ""),' + cr +
    '            reply_to=c.get("reply_to", "") or data.get("username", "author"),' + cr +
    '            avatar_color=clean_hex_color(c.get("avatar_color", "")),' + cr +
    '            avatar_url=c_avatar_file,'
)

if old_cmt_inst in c_ai:
    c_ai = c_ai.replace(old_cmt_inst, new_cmt_inst, 1)
    print("Updated comment avatar assignment in 01_ai_content.py successfully.")
else:
    print("ERROR: old_cmt_inst not found in 01_ai_content.py!")

# Update Post instantiation with new fields
old_post_inst = (
    '        sentence_reveal_mode=data.get("sentence_reveal_mode", "standard_full"),' + cr +
    '        emotional_arc=data.get("emotional_arc", "Tò mò -> Đẩy cao trào -> Cú bẻ lái sốc -> Cạn lời"),'
)

new_post_inst = (
    '        sentence_reveal_mode=data.get("sentence_reveal_mode", "standard_full"),' + cr +
    '        satisfying_category=data.get("satisfying_category", "gameplay"),' + cr +
    '        recommended_theme=data.get("recommended_theme", "light"),' + cr +
    '        story_context_type=ctx_type,' + cr +
    '        cta_question=data.get("cta_question", ""),' + cr +
    '        pacing_tempo=data.get("pacing_tempo", "normal"),' + cr +
    '        avatar_url=(f_avatar_pool[0] if any(k in data.get("username", "").lower() for k in female_keywords) else m_avatar_pool[0]),' + cr +
    '        emotional_arc=data.get("emotional_arc", "Tò mò -> Đẩy cao trào -> Cú bẻ lái sốc -> Cạn lời"),'
)

if old_post_inst in c_ai:
    c_ai = c_ai.replace(old_post_inst, new_post_inst, 1)
    print("Updated Post instantiation in 01_ai_content.py successfully.")
else:
    print("ERROR: old_post_inst not found in 01_ai_content.py!")

with open(ai_path, "w", encoding="utf-8") as f:
    f.write(c_ai)
print("Finished 01_ai_content.py updates.")

