import learnhub as lh

def run():
    modules=[{'id':31427,'modname':'quiz','name':'Mini-Questionnaire','url':lh.SITE+'/mod/quiz/view.php?id=31427','dates':[{'dataid':'timeclose','timestamp':1792616340}],'completiondata':None},
             {'id':2,'modname':'quiz','name':'Hidden','uservisible':False},
             {'id':3,'modname':'feedback','name':'Survey','dates':None},
             {'id':4,'modname':'resource','name':'Slides'},
             {'id':5,'modname':'turnitintooltwo','name':'Paper','dates':[{'dataid':'duedate','timestamp':0},{'dataid':'timeclose','timestamp':123}]} ]
    result=lh.course_activities('SOSC130',[{'modules':modules}])
    assert len(result)==3
    assert result[2]['due']==123 and result[2]['activity_type']=='turnitintooltwo'
    quiz=result[0]
    assert quiz['due']==1792616340 and quiz['activity_type']=='quiz'
    assert quiz['submission_known'] is False and 'submitted' not in quiz
    assert result[1]['due']==0
    assert len(lh.READ_ONLY)==5
    print('PASS: quiz metadata, deadline, unknown submission status, visibility, no extra Moodle permissions')

if __name__=='__main__':run()
