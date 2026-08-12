-- Compiled to an .app so Notification Center attributes the banner to
-- Radon Incident, not Script Editor. Clicking the banner relaunches this
-- applet with no argv and opens Contents/Resources/latest-open.

on readTarget()
	set resourcePath to POSIX path of (path to me as text) & "Contents/Resources/latest-open"
	try
		return do shell script "/usr/bin/head -n 1 " & quoted form of resourcePath
	end try
	return ""
end readTarget

on openTarget()
	set target to readTarget()
	if target is not "" then
		do shell script "/usr/bin/open " & quoted form of target
	end if
end openTarget

on run argv
	if (count of argv) is greater than or equal to 3 then
		set theTitle to item 1 of argv
		set theSubtitle to item 2 of argv
		set theBody to item 3 of argv
		display notification theBody with title theTitle subtitle theSubtitle
	else
		openTarget()
	end if
end run
