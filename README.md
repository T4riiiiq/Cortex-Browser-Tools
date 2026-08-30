# Cortex Browser Tools

سكربتان لجمع معلومات المتصفحات على Windows أثناء أعمال الـDFIR والاستجابة للحوادث، ومجهزان للعمل محليًا أو من خلال **Cortex XDR Endpoint Scripts**.

## السكربتات

- `browser_History.py`: يجمع سجل التصفح، التنزيلات، كلمات البحث المحفوظة، العلامات المرجعية وبيانات الجلسات من Chrome وEdge وBrave وFirefox.
- `browser_extensions.py`: يحصر إضافات المتصفحات ويعرض صلاحياتها، مع تمييز الصلاحيات المهمة أمنيًا.

> السكربتات لا تجمع كلمات المرور أو بيانات الدفع، وتستخدم مكتبات Python القياسية فقط.

## التشغيل محليًا

المتطلبات:

- Windows
- Python 3.7 أو أحدث
- يفضّل تشغيل PowerShell كمسؤول حتى يستطيع السكربت قراءة ملفات جميع المستخدمين.

نزّل المستودع ثم افتح PowerShell داخله:

```powershell
git clone https://github.com/T4riiiiq/Cortex-Browser-Tools.git
cd Cortex-Browser-Tools
```

لجمع سجل المتصفحات:

```powershell
python .\browser_History.py
```

لجمع إضافات المتصفحات:

```powershell
python .\browser_extensions.py
```

بعد انتهاء التشغيل سيظهر JSON يحتوي على `output_path`. هذا هو مسار ملف ZIP النهائي، وغالبًا يكون داخل مجلد Windows المؤقت `%TEMP%`.

## التشغيل من Cortex XDR

لكل سكربت:

1. افتح **Cortex XDR → Action Center → Scripts Library → New Script**.
2. ارفع ملف Python المطلوب.
3. اختر **Windows** كمنصة التشغيل.
4. اختر التشغيل بواسطة **Entry Point** واكتب `main`.
5. لا تضف Parameters.
6. اختر **Dictionary** لنوع المخرجات.
7. ابدأ بمهلة تشغيل قدرها `900` ثانية.
8. شغّل السكربت أولًا على جهاز تجريبي واحد.
9. بعد اكتماله، افتح نتيجة التنفيذ ونزّل الملف الموجود في `files_to_get`.

## قراءة النتائج

### نتائج `browser_History.py`

ابدأ بهذه الملفات داخل ZIP:

- `summary.txt`: ملخص سريع للأعداد والأخطاء.
- `profile_inventory.csv`: المتصفحات والبروفايلات التي تم اكتشافها.
- `artifact_status.csv`: حالة جمع كل نوع من البيانات.
- `errors.csv`: تفاصيل أي فشل أثناء الجمع.

ثم راجع ملفات التحقيق مثل `history.csv` و`downloads.csv` و`search_terms.csv` و`bookmarks.csv` و`sessions.csv`.

### نتائج `browser_extensions.py`

ملف ZIP يحتوي على CSV بأسماء الإضافات وإصداراتها ومساراتها وصلاحياتها. راجع خصوصًا الإضافات التي تحتوي على صلاحيات مهمة أمنيًا مثل `debugger` أو `nativeMessaging` أو `proxy` أو الوصول الواسع للمواقع.

## اختبار سريع قبل الاستخدام الواسع

شغّل كل سكربت على جهاز واحد أولًا، وتأكد أن ZIP تم تنزيله وأن ملفات CSV تحتوي على بيانات فعلية. بعد ذلك اختبره والمتصفح مفتوح، ثم وسّع التشغيل تدريجيًا على أجهزة أخرى.

