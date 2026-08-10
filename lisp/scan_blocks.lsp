;;; scan_blocks.lsp
;;; Run inside AutoCAD to scan a folder of .dwg files and extract attribute tags.
;;;
;;; USAGE (type in AutoCAD command line):
;;;   (load "C:/path/to/lisp/scan_blocks.lsp")
;;;   (mcc-scan-folder "C:/path/to/8PX3 Blocks" "C:/temp/mcc_attdefs.csv")
;;;
;;; OUTPUT:
;;;   A CSV at the path you specify. Then run:
;;;   python scripts/generate_catalog.py --from-csv "C:/temp/mcc_attdefs.csv" --dir "C:/path/to/8PX3 Blocks"

(vl-load-com)

(defun mcc-scan-folder (folder-path out-csv / files f dwg-file)
  (setq files (vl-directory-files folder-path "*.dwg" 1))
  (if (null files)
    (progn (princ (strcat "\nNo .dwg files found in: " folder-path "\n")) (exit))
  )
  (princ (strcat "\nFound " (itoa (length files)) " files. Scanning...\n"))
  (setq f (open out-csv "w"))
  (write-line "filename,tags" f)
  (foreach dwg-name files
    (setq dwg-file (strcat folder-path "\\" dwg-name))
    (princ (strcat "  " dwg-name " ... "))
    (mcc-scan-one-file dwg-file dwg-name f)
    (princ "done\n")
  )
  (close f)
  (princ (strcat "\nDone. CSV written to: " out-csv "\n"))
  (princ)
)


(defun mcc-scan-one-file (dwg-path dwg-name out-file / doc tags tag-str joined)
  (setq doc   nil)
  (setq tags  '())

  ;; Open the file (no read-only flag — more compatible across versions)
  (vl-catch-all-apply
    (function
      (lambda ()
        (setq doc
          (vla-Open
            (vla-get-Documents (vlax-get-acad-object))
            dwg-path
          )
        )
      )
    )
  )

  (if (null doc)
    (progn
      (write-line (strcat "\"" dwg-name "\",ERROR_OPEN") out-file)
      (exit-defun)
    )
  )

  ;; Scan every block definition for AcDbAttributeDefinition entities
  (vl-catch-all-apply
    (function
      (lambda ()
        (vlax-for blk (vla-get-Blocks doc)
          (vl-catch-all-apply
            (function
              (lambda ()
                (if (not (wcmatch (vla-get-Name blk) "`**"))
                  (vlax-for ent blk
                    (vl-catch-all-apply
                      (function
                        (lambda ()
                          (if (= "AcDbAttributeDefinition" (vla-get-ObjectName ent))
                            (setq tags (cons (vla-get-TagString ent) tags))
                          )
                        )
                      )
                    )
                  )
                )
              )
            )
          )
        )
      )
    )
  )

  ;; Also check model space for loose ATTDEFs or ATTRIB instances
  (vl-catch-all-apply
    (function
      (lambda ()
        (vlax-for ent (vla-get-ModelSpace doc)
          (vl-catch-all-apply
            (function
              (lambda ()
                (cond
                  ((= "AcDbAttributeDefinition" (vla-get-ObjectName ent))
                   (setq tags (cons (vla-get-TagString ent) tags)))
                  ((= "AcDbAttribute" (vla-get-ObjectName ent))
                   (setq tags (cons (strcat "INST:" (vla-get-TagString ent)) tags)))
                )
              )
            )
          )
        )
      )
    )
  )

  ;; De-duplicate and reverse to restore original order
  (setq tags (mcc-unique (reverse tags)))

  ;; Join tags with pipe separator
  ;; NOTE: variable named "itm" not "t" — T is a reserved symbol in AutoLISP
  (setq joined "")
  (foreach itm tags
    (if (= joined "")
      (setq joined itm)
      (setq joined (strcat joined "|" itm))
    )
  )

  (write-line (strcat "\"" dwg-name "\",\"" joined "\"") out-file)

  ;; Close without saving — mark as unmodified first to suppress save dialog
  (vl-catch-all-apply (function (lambda () (vla-put-Saved doc :vlax-true))))
  (vl-catch-all-apply (function (lambda () (vla-Close doc))))
)


(defun mcc-unique (lst / seen result)
  (setq seen '() result '())
  (foreach itm lst
    (if (not (member itm seen))
      (progn
        (setq seen   (cons itm seen))
        (setq result (cons itm result))
      )
    )
  )
  (reverse result)
)


(princ "\nscan_blocks.lsp loaded.")
(princ "\nUsage: (mcc-scan-folder \"C:/path/to/blocks\" \"C:/temp/mcc_attdefs.csv\")")
(princ)
