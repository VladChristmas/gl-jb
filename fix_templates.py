import os
import re

template_dir = r"D:\GL_JB\project\app\templates"
for root, _dirs, files in os.walk(template_dir):
    for file in files:
        if file.endswith(".html"):
            filepath = os.path.join(root, file)
            with open(filepath, encoding="utf-8") as f:
                content = f.read()
            # Fix admin routes
            content = re.sub(r"url_for\('(admin_[^']+)'\)", r"url_for('admin.\1')", content)
            content = re.sub(r'url_for\("(admin_[^"]+)"\)', r"url_for('admin.\1')", content)
            with open(filepath, "w", encoding="utf-8") as f:
                f.write(content)
print("Done")
