from pathlib import Path

before_dir = Path('artifacts/girlish-unshackled-20261103/before')
after_dir = Path('whalegirl_linux')

def extract_between(text, start_pattern, end_pattern):
    s = text.find(start_pattern)
    if s == -1: return ""
    e = text.find(end_pattern, s)
    if e == -1: return text[s:]
    return text[s:e]

conv_old = (before_dir / 'conversation.py').read_text('utf-8')
conv_new = (after_dir / 'conversation.py').read_text('utf-8')

aff_old = (before_dir / 'affection.py').read_text('utf-8')
aff_new = (after_dir / 'affection.py').read_text('utf-8')

aut_old = (before_dir / 'autonomy.py').read_text('utf-8')
aut_new = (after_dir / 'autonomy.py').read_text('utf-8')

exp_old = (before_dir / 'experience.py').read_text('utf-8')
exp_new = (after_dir / 'experience.py').read_text('utf-8')

def fn_slice(txt, fn_name):
    start = txt.find(f"def {fn_name}")
    if start == -1: return ""
    next_fn = txt.find("\ndef ", start + 5)
    return txt[start:next_fn] if next_fn != -1 else txt[start:]

def static_prompt_val(txt):
    start = txt.find("STATIC_PROMPT = '''") + len("STATIC_PROMPT = '''")
    end = txt.find("'''", start)
    return txt[start:end].strip()

print("--- 提示词与函数实现字数详细统计 ---")
# 1. autonomy
p_aut_old = static_prompt_val(aut_old)
p_aut_new = static_prompt_val(aut_new)
print(f"1. autonomy.py STATIC_PROMPT: {len(p_aut_old)} -> {len(p_aut_new)} 字符 (精简 {len(p_aut_old)-len(p_aut_new)} 字, -{(len(p_aut_old)-len(p_aut_new))/len(p_aut_old)*100:.1f}%)")

# 2. experience
p_exp_old = static_prompt_val(exp_old)
p_exp_new = static_prompt_val(exp_new)
print(f"2. experience.py STATIC_PROMPT: {len(p_exp_old)} -> {len(p_exp_new)} 字符 (精简 {len(p_exp_old)-len(p_exp_new)} 字, -{(len(p_exp_old)-len(p_exp_new))/len(p_exp_old)*100:.1f}%)")

# 3. habit_static_prompt
f_hbt_old = fn_slice(conv_old, "habit_static_prompt")
f_hbt_new = fn_slice(conv_new, "habit_static_prompt")
print(f"3. conversation.py habit_static_prompt: {len(f_hbt_old)} -> {len(f_hbt_new)} 字符 (精简 {len(f_hbt_old)-len(f_hbt_new)} 字, -{(len(f_hbt_old)-len(f_hbt_new))/len(f_hbt_old)*100:.1f}%)")

# 4. sticker_static_prompt
f_stk_old = fn_slice(conv_old, "sticker_static_prompt")
f_stk_new = fn_slice(conv_new, "sticker_static_prompt")
print(f"4. conversation.py sticker_static_prompt: {len(f_stk_old)} -> {len(f_stk_new)} 字符 (精简 {len(f_stk_old)-len(f_stk_new)} 字, -{(len(f_stk_old)-len(f_stk_new))/len(f_stk_old)*100:.1f}%)")

# 5. affection venue_prompt & static_prompt
f_ven_old = fn_slice(aff_old, "venue_prompt")
f_ven_new = fn_slice(aff_new, "venue_prompt")
print(f"5. affection.py venue_prompt: {len(f_ven_old)} -> {len(f_ven_new)} 字符 (聚焦唯一主人铁律 & 释放破甲尺度)")

f_sta_old = fn_slice(aff_old, "static_prompt")
f_sta_new = fn_slice(aff_new, "static_prompt")
print(f"6. affection.py static_prompt: {len(f_sta_old)} -> {len(f_sta_new)} 字符 (精简 {len(f_sta_old)-len(f_sta_new)} 字)")
