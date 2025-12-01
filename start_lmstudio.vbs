Set WshShell = CreateObject("WScript.Shell")

' LM Studio 경로 후보들
paths = Array( _
    "C:\Users\" & CreateObject("WScript.Network").UserName & "\AppData\Local\Programs\LM Studio\LM Studio.exe", _
    "C:\Users\" & CreateObject("WScript.Network").UserName & "\AppData\Local\LM Studio\LM Studio.exe", _
    "C:\Program Files\LM Studio\LM Studio.exe", _
    "C:\Program Files (x86)\LM Studio\LM Studio.exe" _
)

' 파일 시스템 객체 생성
Set fso = CreateObject("Scripting.FileSystemObject")

' LM Studio가 이미 실행 중인지 확인
Set objWMIService = GetObject("winmgmts:\\.\root\cimv2")
Set colProcesses = objWMIService.ExecQuery("Select * from Win32_Process Where Name = 'LM Studio.exe'")

If colProcesses.Count > 0 Then
    WScript.Echo "LM Studio가 이미 실행 중입니다."
    WScript.Quit 0
End If

' 경로 순서대로 확인
lmstudioPath = ""
For Each path In paths
    If fso.FileExists(path) Then
        lmstudioPath = path
        Exit For
    End If
Next

If lmstudioPath = "" Then
    WScript.Echo "LM Studio 실행 파일을 찾을 수 없습니다."
    WScript.Quit 1
End If

' LM Studio를 최소화 상태로 실행
' 0 = 숨김, 1 = 일반, 2 = 최소화, 3 = 최대화, 7 = 최소화(포커스 없음)
WshShell.Run """" & lmstudioPath & """", 7, False

WScript.Echo "LM Studio가 최소화 상태로 시작되었습니다."
WScript.Quit 0






