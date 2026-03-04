<!-- image-uuid: 1f11733d-8e23-691f-a92a-34cc56d32737 -->

Module Assessment Sheet for 2025-2026

| Module             | Introduction to Image Processing (IIP) / COMP 2005 / Semester 2   |
|--------------------|-------------------------------------------------------------------|
| Assessment         | Coursework (100%)                                                 |
| Module Convenor(s) | Magan Moemeni                                                     |

| Programming and Conference Paper   | Group   |
|------------------------------------|---------|

## Description and Deliverable(s)

| Release Date                                                        | Friday, 27 February 2026                                                                                                                                   |
|---------------------------------------------------------------------|------------------------------------------------------------------------------------------------------------------------------------------------------------|
| Submission Date                                                     | Friday, 06 May 2026 by 16:00pm via UoN's Moodle                                                                                                            |
| Late Policy (University of Nottingham default will apply, if blank) | Work submitted after the deadline will be subject to a penalty of 5 marks (the standard 5% absolute) for each late working day out of the total 100 marks. |
| Feedback Mechanism and Date                                         | Marks and written feedback will be returned via Moodle on the w/c 2 June 2026                                                                              |

The coursework (details below) requires you to work in groups of maximums of 4 to develop a software solution, using Python, to a real image processing problem and collaboratively producing a conference paper describing and critically evaluating your solution. The deliverables required are:

## Per group submission:

1. Python codes: .py files (submitted as a ZIP folder)

2. A 6-page conference paper in PDF format ( note: additional pages will result in a deduction of 5% for every additional page for the entire group ).

3. Results 2026 IIP - Group XXX (submitted as a ZIP folder and please change XXX to your group number (e.g., 001, 007, 011)

4. -001 - Input Images

5. -002 - Image Processing Pipeline

6. -003 - Output Images

## Individual submission:

4. Each of you will need to make an additional submission, separately for your peer assessment . Please click on the ' Peer Assessment ' link on Moodle to perform this submission.

5. Additionally, each of you will need to fill up the provided AI Declaration Microsoft Form link on Moodle. Should you fail to perform a submission, it WILL result in a penalty of 15% deduction of your individual mark.

Aside from this assessment sheet , you will be provided with the following documentations:

1. COMP2005-CW-GroupXXX.docx : this is your conference paper template. Please change XXX to your group number (e.g., 001, 007, 011). Also, please do not change the formatting and headers. Marks will be deducted if submitted document does not follow the original formatting style. The provided table as well as text coloured red and blue, respectively are to be replaced by your own written words.

2. A compressed Dataset folder.

3. A compressed Ground Truth folder.

Note: COMP2005-CW-GroupXXX.docx is to be converted to PDF prior to submission and all text should be coloured in black.

## Declaration of AI Use

As you are aware, Artificial Intelligence (AI) - especially tools like ChatGPT, Microsoft Copilot, and Grammarly-is rapidly transforming how we learn, write, code, and even think. For this module, we like to engage critically with AI: not to fear it, but to use it wisely and responsibly.

For this assessment, you must submit the AI Use Declaration form (to be provided on Moodle), clearly stating whether AI tools were used and how. This new practice supports transparency and academic integrity.

REMEMBER : Undeclared or inappropriate use of AI tools may constitute academic misconduct, in line with University of Nottingham (UoN) regulations.

<!-- image-uuid: 1f11733d-8f05-606d-8711-499dae3e9330 -->

## Semantic Segmentation of Blood Cell

## Background

The need to separate one material from another, or to identify a particular material, arises in many image processing applications. From industrial inspection, where the task may involve identifying pixels corresponding to correctly manufactured cloth, to human computer interaction, where detecting human skin is required, the core problem remains the same: developing an image processing pipeline that converts a colour image into a new representation by labelling different materials.

Medical image analysis is an active research area undergoing transformative evolution through the application of image processing and computer vision technologies. In the field of haematology, for example, Ali et al. (2023) state that haematologists bear the burden of labour-intensive manual processes, requiring meticulous examination of countless blood cells under high workloads and frequent distractions to diagnose blood disorders and guide treatment decisions. However, advancements in automated peripheral blood smear analysis have significantly alleviated these challenges faced by haematologists.

In this coursework, the objective is to develop an image processing pipeline to segment pro-myelocytes (PMYs) from their corresponding background. The resulting pipeline will generate relevant output images to automate blood cell classification.

## Image Processing Pipeline

An image processing pipeline usually includes at least some of the following steps (but not exhaustively):

Colour space conversion : choose a colour space. Any can be used, but most people choose to work in a lowerdimensional (&lt;3) space whenever possible.

Noise reduction : depending on image quality, some form of noise suppression may be required.

Thresholding/Segmentation : image regions corresponding to blood cells must be identified, and thresholding is a common approach. The method used varies and may be global or local. Methods that automatically determine the threshold value have obvious advantages over those that require user interaction.

Binary image processing : Identifying a perfect threshold value or segmentation method is almost impossible, and most methods will result in some mis-classified pixels. A further binary image processing stage is often needed to clean up the image, hopefully leaving it containing only regions that correspond to required blood cells. Nothing, however, is perfect.

## Dataset

You will be using the Naturalize dataset, consisting of 16,000 images, separated into 11 classes. For this pipeline we have selected 3 out of these 11 classes PMY , EO and ERB . Within your team you should analyse these images in each folder and make a decision as a team for the images to use for this pipeline. You must choose ONLY 5 images for each folder.

Empty Folders of Easy, Medium and Hard are provided to save 5 images from PM, EO and ERB in each folder.

## Image Selection Guide :

While you can make your own judgment (provide explanation on why you selected those sets of images in each folder), here is a guideline based for each folder : PMY for Easy ; EO for Medium ; ERB for Hard

## Ground Truth

You will also be provided with a Ground Truth folder which contins images that will be used to fulfil the pipeline evaluation of your coursework. Your image processing pipeline should automatically generate mask of the PMY as shown in Figure 1, below. To ease the process, please add ' \_mymask ' at the end of each name of the image such as ' image\_1\_mymask.png '.

Figure 1 : An example of a mask created for one PMY.

<!-- image-uuid: 1f11733d-8f20-6e00-b68b-1f84a07f2493 -->

<!-- image-uuid: 1f11733d-8f36-6d3c-aa9b-770b8721cdf8 -->

## Your ACTION as a Group

Design and implement a single Python program that inputs one of the images from the provided dataset at a time and outputs an output image which has a white background with ONLY the segmented PMY as shown in Figure 2. You may not need to employ all the steps listed above, but you will probably find it worthwhile to at least consider them all. You are also encouraged to use other strategies covered in this module to address the PMY segmentation problem.

The focus of this coursework is on designing and developing your image processing pipeline to perform the abovementioned tasks. You must make sure to evaluate the final outcome. The evaluation can be both qualitative for example perceptual measures - and quantitative - for example mIoU (mean Intersection over Union)can be use as the metrics to evaluate your image processing pipeline's performance. More evaluation metrics ( that you can use ) for image segmentation will be covered in Lecture 9 .

You should seek a solution that is as automatic as possible (try to minimise the number of user-supplied parameters) and operates on all the provided images without user intervention (one parameter set for all).

Figure 2 : Segmentation Desired Outcome

<!-- image-uuid: 1f11733d-8f56-6fcb-a16c-317a08c156c6 -->

For your conference paper, you should identify 15 images ( 5 easy, 5 medium &amp; 5 hard ) to support your arguments of your proposed image processing pipeline.

As for your code, you are required to make your code easy to be executed by the marker. In other words, the marker can run your code and reproduce your given outputs. After the execution of your program, it is expected that each image in your provided input-images folder (e.g., 15 images - 5 easy, 5 medium &amp; 5 hard) has a corresponding segmentation result represented by a .jpg file saved in the output-images folder , categorised within three sub-folders (e.g., easy, medium, and hard) with the same name as the input images but with an extension of '-easy\_1, -easy\_2, -easy\_3, -medium\_1, -medium\_2, -medium\_3, -hard\_1, -hard\_2 and -hard\_3 ' at the end of the filename. As mentioned earlier, your output image must have a white background with only the segmented PMY. Additionally, images generated as part of your image processing pipeline can be saved in the image-processing-pipeline folder whereby you can create subfolders for each input image and label the sub-folders as the same name as the input image. For everyone convenience, I have provided empty folders/subfolders (e.g., Results 2026 IIP - Group XXX , where XXX refers to your group number 001, 007, 011), following the above-mentioned naming convention. All you have to do is fill it up with your desired 15 input images, image processing pipeline images and corresponding output images.

NOTE : A readme file is recommended to be produced alongside the source code.

<!-- image-uuid: 1f11733d-903d-6aa0-99eb-38343303cf50 -->

Figure 3 : The overall process of Image Processing Pipeline and Evaluation

<!-- image-uuid: 1f11733d-9050-691c-8032-4ed9c463dd85 -->

## CONFERENCE PAPER : When writing your conference paper, consider the following points:

1. Detail the chosen method(s) and specific image processing technique(s) employed.

2. Explain why you choose those technique(s) and method(s). You may include the pseudocodes of the chosen technique(s) and method(s) (if applicable). Clearly justify your parameter choices and support your arguments based on either the literature review or trial and error.

3. Present the results obtained from your practical implementation.

4. Critically evaluate your method(s) on the basis of those results: what are its strengths and weakness? This section should make explicit reference to features of the results you obtained as well as using the relevant metric for segmentation to support your provided arguments/justifications.

5. Critically evaluate your results: highlighting the strengths and weakness of your entire pipeline. Please use the qualitative metrics (e.g., MIoU ) and qualitative metrics (perception measurements), generated from your pipeline to further support your provided arguments/justifications in your conference paper.

## Reference:

Abou Ali, M., Dornaika, F., &amp; Arganda-Carreras, I. (2023). Blood cell revolution: Unveiling 11 distinct types with 'Naturalize' augmentation. Algorithms , 16 (12), 562.

## Assessment Criteria

Python code(s): 30% [ unable to run codes will result in 0% being awarded ]

## Conference Paper:

- -Justification of chosen algorithms based on performed literature review: 15%

- -Critical analysis and discussion of your findings: 25%

- -Discussion of the strengths and weaknesses of your proposed pipeline (based on your findings and pipeline design) : 30%

## ExamSys Test

## Individual 30%

| Description        | Focuses on theories covered in the lectures and practical exercises   |
|--------------------|-----------------------------------------------------------------------|
| Date, Time & Venue | 30 April 2026 &#124; Time and Venue TBC                               |